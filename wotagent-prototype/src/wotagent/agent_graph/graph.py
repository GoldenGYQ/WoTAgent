"""State graph that keeps LLM intelligence and deterministic control explicit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..domain import PolicyDecision, TaskPlan, TaskStatus, TaskTrace
from ..orchestration import TaskOrchestrator
from ..policy import PolicyEngine
from ..telemetry import InMemoryTraceStore


class Planner(Protocol):
    async def __call__(self, state: "AgentState") -> TaskPlan: ...


ResponseComposer = Callable[["AgentState"], str | Awaitable[str]]
RecoveryPlanner = Callable[["AgentState"], TaskPlan | None | Awaitable[TaskPlan | None]]


class AgentState(TypedDict, total=False):
    request_id: str
    session_id: str
    user_id: str
    role: str
    message: str
    context: dict[str, Any]
    plan: TaskPlan
    policy_decisions: list[PolicyDecision]
    trace: TaskTrace
    response: str
    error: str
    phase: str
    confirmation_approved: bool
    recovery_attempts: int
    task_id: str


@dataclass(frozen=True)
class AgentGraphDependencies:
    planner: Planner
    orchestrator: TaskOrchestrator
    policy: PolicyEngine
    responder: ResponseComposer | None = None
    recovery_planner: RecoveryPlanner | None = None
    max_recovery_attempts: int = 1
    trace_store: InMemoryTraceStore | None = None


def build_agent_graph(deps: AgentGraphDependencies, *, checkpointer: Any = None):
    """Build a fresh graph; tests should provide a fresh checkpointer per case."""

    async def plan_node(state: AgentState) -> dict[str, Any]:
        await _record(deps, state, "agent.node.started", "planner")
        try:
            plan = await deps.planner(state)
            await _record(deps, state, "agent.node.completed", "planner", {
                "plan": plan.model_dump(mode="json"),
            })
            return {"plan": plan, "phase": "planned", "error": ""}
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
                    await _record(deps, state, "agent.tool.completed", "executor", {
                        "step_id": item.step.id,
                        "tool": "query_state" if item.step.kind == "query" else "control_device",
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
                iteration=state.get("recovery_attempts", 0) + 1,
            )
        return {"trace": trace, "phase": "executed"}

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
        if deps.responder is not None:
            value = deps.responder(state)
            response = await value if hasattr(value, "__await__") else value
            return {"response": str(response), "phase": "responded"}
        return {"response": _default_response(state), "phase": "responded"}

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

    def after_execute(state: AgentState) -> Literal["recover", "respond"]:
        trace = state["trace"]
        if (
            trace.status == TaskStatus.FAILED
            and deps.recovery_planner is not None
            and state.get("recovery_attempts", 0) < deps.max_recovery_attempts
        ):
            return "recover"
        return "respond"

    def after_recover(state: AgentState) -> Literal["policy", "respond"]:
        return "policy" if state.get("phase") == "replanned" else "respond"

    builder = StateGraph(AgentState)
    builder.add_node("plan", plan_node)
    builder.add_node("policy", policy_node)
    builder.add_node("execute", execute_node)
    builder.add_node("confirm", confirmation_node)
    builder.add_node("recover", recover_node)
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
        {"recover": "recover", "respond": "respond"},
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
