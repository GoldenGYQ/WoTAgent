"""State graph that keeps LLM intelligence and deterministic control explicit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from ..domain import PolicyDecision, TaskPlan, TaskStatus, TaskTrace
from ..orchestration import TaskOrchestrator
from ..policy import PolicyEngine


class Planner(Protocol):
    async def __call__(self, state: "AgentState") -> TaskPlan: ...


ResponseComposer = Callable[["AgentState"], str | Awaitable[str]]


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


@dataclass(frozen=True)
class AgentGraphDependencies:
    planner: Planner
    orchestrator: TaskOrchestrator
    policy: PolicyEngine
    responder: ResponseComposer | None = None


def build_agent_graph(deps: AgentGraphDependencies, *, checkpointer: Any = None):
    """Build a fresh graph; tests should provide a fresh checkpointer per case."""

    async def plan_node(state: AgentState) -> dict[str, Any]:
        try:
            plan = await deps.planner(state)
            return {"plan": plan, "phase": "planned", "error": ""}
        except Exception as exc:
            return {"phase": "planning_failed", "error": str(exc)}

    async def policy_node(state: AgentState) -> dict[str, Any]:
        plan = state["plan"]
        decisions = [deps.policy.evaluate(state.get("role", "viewer"), step) for step in plan.steps]
        return {"policy_decisions": decisions, "phase": "policy_checked"}

    async def execute_node(state: AgentState) -> dict[str, Any]:
        trace = await deps.orchestrator.execute(state["plan"], role=state.get("role", "viewer"))
        return {"trace": trace, "phase": "executed"}

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

    def after_policy(state: AgentState) -> Literal["execute", "respond"]:
        decisions = state.get("policy_decisions", [])
        if any(item.effect in {"deny", "confirm"} for item in decisions):
            return "respond"
        return "execute"

    builder = StateGraph(AgentState)
    builder.add_node("plan", plan_node)
    builder.add_node("policy", policy_node)
    builder.add_node("execute", execute_node)
    builder.add_node("respond", respond_node)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", after_plan, {"policy": "policy", "respond": "respond"})
    builder.add_conditional_edges("policy", after_policy, {"execute": "execute", "respond": "respond"})
    builder.add_edge("execute", "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


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
