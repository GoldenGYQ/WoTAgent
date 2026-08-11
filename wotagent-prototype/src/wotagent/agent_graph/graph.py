"""State graph that keeps LLM intelligence and deterministic control explicit."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..domain import PolicyDecision, TaskPlan, TaskStatus, TaskTrace
from ..orchestration import TaskOrchestrator
from ..policy import PolicyEngine
from ..telemetry import InMemoryTraceStore


class Planner(Protocol):
    async def __call__(self, state: AgentState) -> TaskPlan: ...


ResponseComposer = Callable[["AgentState"], str | Awaitable[str]]
RecoveryPlanner = Callable[["AgentState"], TaskPlan | None | Awaitable[TaskPlan | None]]


class AgentState(TypedDict, total=False):
    request_id: str
    session_id: str
    user_id: str
    role: str
    message: str
    conversation_history: list[dict[str, str]]
    context: dict[str, Any]
    plan: TaskPlan
    policy_decisions: list[PolicyDecision]
    trace: TaskTrace
    response: str
    error: str
    phase: str
    confirmation_approved: bool
    recovery_attempts: int
    agent_iterations: int
    tool_call_count: int
    execution_history: list[TaskTrace]
    plan_signatures: list[str]
    task_id: str
    thinking: bool


@dataclass(frozen=True)
class AgentGraphDependencies:
    planner: Planner
    orchestrator: TaskOrchestrator
    policy: PolicyEngine
    responder: ResponseComposer | None = None
    recovery_planner: RecoveryPlanner | None = None
    max_recovery_attempts: int = 1
    max_agent_iterations: int = 4
    max_tool_calls: int = 8
    trace_store: InMemoryTraceStore | None = None


def build_agent_graph(deps: AgentGraphDependencies, *, checkpointer: Any = None):
    """Build a fresh graph; tests should provide a fresh checkpointer per case."""

    async def plan_node(state: AgentState) -> dict[str, Any]:
        await _record(deps, state, "agent.node.started", "planner")
        try:
            capabilities = await deps.orchestrator.prepare()
            context = dict(state.get("context", {}))
            if capabilities is not None:
                context["devices"] = capabilities
            if state.get("context", {}).get("execution_mode") == "observe_act_loop":
                context["agent_loop"] = _agent_loop_context(state, deps)
            planner_state = {**state, "context": context}
            plan = await deps.planner(planner_state)
            signature = _plan_signature(plan)
            signatures = list(state.get("plan_signatures", []))
            if state.get("agent_iterations", 0) and signature in signatures:
                await _record(
                    deps,
                    state,
                    "agent.loop.terminated",
                    "loop_control",
                    {
                        "reason": "duplicate_plan",
                        "plan": plan.model_dump(mode="json"),
                    },
                    iteration=state.get("agent_iterations", 0),
                )
                return {
                    "plan": plan,
                    "phase": "loop_stalled",
                    "error": "agent loop repeated an already executed plan",
                    "context": context,
                }
            signatures.append(signature)
            await _record(deps, state, "agent.node.completed", "planner", {
                "plan": plan.model_dump(mode="json"),
            })
            result: dict[str, Any] = {
                "plan": plan,
                "phase": "planned",
                "error": "",
                "context": context,
                "plan_signatures": signatures,
            }
            if state.get("agent_iterations", 0):
                result["confirmation_approved"] = False
            return result
        except Exception as exc:
            await _record(deps, state, "agent.node.failed", "planner", {"error": str(exc)})
            return {"phase": "planning_failed", "error": str(exc)}

    async def policy_node(state: AgentState) -> dict[str, Any]:
        plan = state["plan"]
        decisions = [deps.policy.evaluate(state.get("role", "viewer"), step) for step in plan.steps]
        await _record(deps, state, "policy.evaluated", "policy", {
            "decisions": [item.model_dump(mode="json") for item in decisions],
        })
        return {"policy_decisions": decisions, "phase": "policy_checked"}

    async def execute_node(state: AgentState) -> dict[str, Any]:
        trace = await deps.orchestrator.execute(
            state["plan"],
            role=state.get("role", "viewer"),
            confirmed=state.get("confirmation_approved", False),
            task_id=state.get("task_id"),
        )
        if deps.trace_store is not None:
            for item in trace.steps:
                if item.execution is not None:
                    default_tool = "query_state" if item.step.kind == "query" else "control_device"
                    await _record(deps, state, "agent.tool.completed", "executor", {
                        "step_id": item.step.id,
                        "tool": item.execution.raw_result.get("_mcp_tool", default_tool),
                        "input": item.step.model_dump(mode="json"),
                        "output": item.execution.model_dump(mode="json"),
                    })
                if item.verification is not None:
                    await _record(deps, state, "verification.completed", "verifier", {
                        "step_id": item.step.id,
                        **item.verification.model_dump(mode="json"),
                    })
            await _record(
                deps,
                state,
                "agent.loop.completed",
                "orchestrator",
                {"status": trace.status.value},
                iteration=state.get("agent_iterations", 0) + 1,
            )
        execution_history = [*state.get("execution_history", []), trace]
        return {
            "trace": trace,
            "execution_history": execution_history,
            "agent_iterations": state.get("agent_iterations", 0) + 1,
            "tool_call_count": state.get("tool_call_count", 0) + len(trace.steps),
            "phase": "executed",
        }

    async def loop_control_node(state: AgentState) -> dict[str, Any]:
        iteration = state.get("agent_iterations", 0)
        tool_calls = state.get("tool_call_count", 0)
        if iteration >= deps.max_agent_iterations:
            await _record(
                deps,
                state,
                "agent.loop.terminated",
                "loop_control",
                {
                    "reason": "max_iterations",
                    "max_iterations": deps.max_agent_iterations,
                    "tool_calls": tool_calls,
                },
                iteration=iteration,
            )
            return {
                "phase": "loop_iteration_limit",
                "error": "agent loop reached the maximum iteration limit before proving the goal complete",
            }
        if tool_calls >= deps.max_tool_calls:
            await _record(
                deps,
                state,
                "agent.loop.terminated",
                "loop_control",
                {
                    "reason": "max_tool_calls",
                    "max_tool_calls": deps.max_tool_calls,
                    "tool_calls": tool_calls,
                },
                iteration=iteration,
            )
            return {
                "phase": "loop_tool_limit",
                "error": "agent loop reached the maximum tool-call limit before proving the goal complete",
            }
        await _record(
            deps,
            state,
            "agent.loop.continued",
            "loop_control",
            {
                "reason": "new observations require another decision",
                "tool_calls": tool_calls,
            },
            iteration=iteration,
        )
        return {"phase": "observe_replan"}

    async def confirmation_node(state: AgentState) -> dict[str, Any]:
        plan = state["plan"]
        decision = interrupt({
            "type": "confirmation_required",
            "plan_id": plan.plan_id,
            "summary": plan.summary,
            "steps": [step.model_dump(mode="json") for step in plan.steps],
        })
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        return {
            "confirmation_approved": approved,
            "phase": "confirmation_approved" if approved else "confirmation_rejected",
        }

    async def recover_node(state: AgentState) -> dict[str, Any]:
        attempts = state.get("recovery_attempts", 0) + 1
        if deps.recovery_planner is None:
            return {"recovery_attempts": attempts, "phase": "recovery_unavailable"}
        value = deps.recovery_planner(state)
        recovered_plan = await value if hasattr(value, "__await__") else value
        if recovered_plan is None:
            return {"recovery_attempts": attempts, "phase": "recovery_declined"}
        await _record(
            deps,
            state,
            "agent.recovery.completed",
            "recovery",
            {"new_plan": recovered_plan.model_dump(mode="json")},
            iteration=attempts,
        )
        return {
            "plan": recovered_plan,
            "recovery_attempts": attempts,
            "confirmation_approved": False,
            "phase": "replanned",
        }

    async def respond_node(state: AgentState) -> dict[str, Any]:
        await _record(deps, state, "agent.node.started", "responder")
        if deps.responder is not None:
            try:
                value = deps.responder(state)
                response = await value if hasattr(value, "__await__") else value
                await _record(deps, state, "agent.node.completed", "responder", {
                    "response": str(response), "fallback": False,
                })
                return {"response": str(response), "phase": "responded"}
            except Exception as exc:
                fallback = _default_response(state)
                await _record(deps, state, "agent.node.failed", "responder", {
                    "error": str(exc), "fallback": fallback,
                })
                return {"response": fallback, "phase": "responded"}
        fallback = _default_response(state)
        await _record(deps, state, "agent.node.completed", "responder", {
            "response": fallback, "fallback": True,
        })
        return {"response": fallback, "phase": "responded"}

    def after_plan(state: AgentState) -> Literal["policy", "respond"]:
        if state.get("error") or state["plan"].intent in {"chat", "clarify"}:
            return "respond"
        return "policy"

    def after_policy(state: AgentState) -> Literal["confirm", "execute", "respond"]:
        decisions = state.get("policy_decisions", [])
        if any(item.effect == "deny" for item in decisions):
            return "respond"
        if any(item.effect == "confirm" for item in decisions):
            return "confirm"
        return "execute"

    def after_confirmation(state: AgentState) -> Literal["execute", "respond"]:
        return "execute" if state.get("confirmation_approved") else "respond"

    def after_execute(state: AgentState) -> Literal["recover", "loop_control", "respond"]:
        trace = state["trace"]
        if (
            trace.status == TaskStatus.FAILED
            and deps.recovery_planner is not None
            and state.get("recovery_attempts", 0) < deps.max_recovery_attempts
        ):
            return "recover"
        if (
            trace.status in {TaskStatus.SUCCEEDED, TaskStatus.PARTIAL_SUCCESS}
            and state.get("context", {}).get("execution_mode") == "observe_act_loop"
        ):
            return "loop_control"
        return "respond"

    def after_loop_control(state: AgentState) -> Literal["plan", "respond"]:
        return "plan" if state.get("phase") == "observe_replan" else "respond"

    def after_recover(state: AgentState) -> Literal["policy", "respond"]:
        return "policy" if state.get("phase") == "replanned" else "respond"

    builder = StateGraph(AgentState)
    builder.add_node("plan", plan_node)
    builder.add_node("policy", policy_node)
    builder.add_node("execute", execute_node)
    builder.add_node("confirm", confirmation_node)
    builder.add_node("recover", recover_node)
    builder.add_node("loop_control", loop_control_node)
    builder.add_node("respond", respond_node)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", after_plan, {"policy": "policy", "respond": "respond"})
    builder.add_conditional_edges(
        "policy",
        after_policy,
        {"confirm": "confirm", "execute": "execute", "respond": "respond"},
    )
    builder.add_conditional_edges(
        "confirm",
        after_confirmation,
        {"execute": "execute", "respond": "respond"},
    )
    builder.add_conditional_edges(
        "execute",
        after_execute,
        {"recover": "recover", "loop_control": "loop_control", "respond": "respond"},
    )
    builder.add_conditional_edges(
        "loop_control",
        after_loop_control,
        {"plan": "plan", "respond": "respond"},
    )
    builder.add_conditional_edges(
        "recover",
        after_recover,
        {"policy": "policy", "respond": "respond"},
    )
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


async def _record(
    deps: AgentGraphDependencies,
    state: AgentState,
    event_type: str,
    node: str,
    data: dict[str, Any] | None = None,
    *,
    iteration: int = 0,
) -> None:
    if deps.trace_store is None or not state.get("task_id"):
        return
    await deps.trace_store.record(
        task_id=state["task_id"],
        type=event_type,
        node=node,
        data=data,
        iteration=iteration,
    )


def _default_response(state: AgentState) -> str:
    if state.get("error"):
        return f"任务规划失败：{state['error']}"
    plan = state.get("plan")
    if plan is None:
        return "任务未生成有效计划。"
    if plan.intent == "chat":
        return plan.summary or "你好，我可以帮助查询和控制已注册的物联网设备。"
    if plan.intent == "clarify":
        return plan.summary or "需要补充设备或操作信息。"
    if state.get("confirmation_approved") is False:
        return "操作已取消，未调用任何设备。"

    decisions = state.get("policy_decisions", [])
    denied = next((item for item in decisions if item.effect == "deny"), None)
    if denied:
        return f"任务未执行：{denied.reason}"
    confirmation = next((item for item in decisions if item.effect == "confirm"), None)
    if confirmation:
        return f"该操作需要确认：{confirmation.reason}"

    trace = state.get("trace")
    if trace is None:
        return "任务未进入执行阶段。"
    messages = {
        TaskStatus.SUCCEEDED: "任务已执行并通过设备状态验证。",
        TaskStatus.PARTIAL_SUCCESS: "任务部分完成，部分步骤失败。",
        TaskStatus.DENIED: "任务因权限或策略限制未执行。",
        TaskStatus.WAITING_CONFIRMATION: "任务正在等待确认。",
        TaskStatus.FAILED: "任务执行失败或设备状态未达到目标。",
    }
    return messages.get(trace.status, f"任务状态：{trace.status.value}")


def _agent_loop_context(
    state: AgentState,
    deps: AgentGraphDependencies,
) -> dict[str, Any]:
    upstream_findings = list(
        state.get("context", {}).get("dependency_findings", [])
    )
    upstream_observations = [
        observation
        for finding in upstream_findings
        if isinstance(finding, dict)
        for observation in finding.get("observations", [])
        if isinstance(observation, dict)
    ]
    return {
        "execution_mode": "observe_act_loop",
        "original_objective": (
            state.get("context", {}).get("original_message")
            or state.get("message", "")
        ),
        "iteration": state.get("agent_iterations", 0) + 1,
        "max_iterations": deps.max_agent_iterations,
        "tool_call_count": state.get("tool_call_count", 0),
        "max_tool_calls": deps.max_tool_calls,
        "upstream_findings": upstream_findings,
        "observations": upstream_observations + [
            observation
            for iteration, trace in enumerate(state.get("execution_history", []), start=1)
            for observation in _trace_observations(trace, iteration)
        ],
    }


def _trace_observations(trace: TaskTrace, iteration: int) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for item in trace.steps:
        execution = item.execution
        observation = {
            "iteration": iteration,
            "step_id": item.step.id,
            "kind": item.step.kind,
            "device_id": item.step.device_id,
            "action": item.step.action,
            "policy": item.policy.effect,
            "execution_status": execution.status if execution is not None else "not_executed",
            "result": execution.raw_result if execution is not None else None,
            "error": execution.error if execution is not None else None,
        }
        if item.verification is not None:
            observation["verification"] = item.verification.model_dump(mode="json")
        observations.append(observation)
    return observations


def _plan_signature(plan: TaskPlan) -> str:
    return plan.model_dump_json(
        exclude={"plan_id", "summary", "assumptions"},
        exclude_none=True,
    )
