"""Hierarchical Butler graph with explicit Environment and Care subgraphs."""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ...telemetry import InMemoryTraceStore
from .actions import EldercareActionService
from .models import ChildAgentResult, DelegatedTask, RiskEvent
from .strategies.rule_based import legacy_delegation_plan


class EldercareState(TypedDict, total=False):
    task_id: str
    user_role: str
    event: dict[str, Any]
    delegations: list[dict[str, Any]]
    delegation: dict[str, Any]
    child_results: Annotated[list[dict[str, Any]], operator.add]
    denied_delegations: list[dict[str, Any]]
    response: str
    status: str


class ChildOutput(TypedDict):
    child_results: list[dict[str, Any]]


DelegationPlanner = Callable[[RiskEvent], list[DelegatedTask] | Awaitable[list[DelegatedTask]]]


LEGACY_DELEGATION_POLICIES: dict[str, dict[str, Any]] = {
    "environment_agent": {
        "allowed_mcp": "eldercare-environment",
        "allowed_actions": {"adjust_temperature"},
    },
    "care_agent": {
        "allowed_mcp": "eldercare-care",
        "allowed_actions": {"notify_caregiver", "create_care_task"},
    },
}


def default_delegation_plan(event: RiskEvent) -> list[DelegatedTask]:
    return legacy_delegation_plan(event)


def build_eldercare_butler_graph(
    action_service: EldercareActionService,
    *,
    trace_store: InMemoryTraceStore | None = None,
    planner: DelegationPlanner = default_delegation_plan,
    checkpointer: Any = None,
):
    async def butler_plan(state: EldercareState) -> dict[str, Any]:
        await _record(trace_store, state, "agent.node.started", "butler")
        event = RiskEvent.model_validate(state["event"])
        value = planner(event)
        tasks = await value if hasattr(value, "__await__") else value
        await _record(trace_store, state, "agent.delegation.created", "butler", {
            "event_id": event.event_id,
            "delegations": [task.model_dump(mode="json") for task in tasks],
        })
        return {
            "delegations": [task.model_dump(mode="json") for task in tasks],
            "child_results": [],
            "status": "delegated",
        }

    async def authorize(state: EldercareState) -> dict[str, Any]:
        resident_id = str(state["event"]["resident_id"])
        user_role = state.get("user_role", "system")
        allowed: list[dict[str, Any]] = []
        denied: list[dict[str, Any]] = []
        for raw in state.get("delegations", []):
            task = DelegatedTask.model_validate(raw)
            spec = LEGACY_DELEGATION_POLICIES.get(task.agent_id)
            reason = ""
            if spec is None:
                reason = "agent is not registered"
            elif user_role == "viewer" and task.actions:
                reason = "viewer role cannot execute eldercare actions"
            elif task.allowed_mcp != spec["allowed_mcp"]:
                reason = "MCP scope does not match agent identity"
            elif resident_id not in task.resident_scope:
                reason = "resident is outside delegated scope"
            elif any(action.name not in spec["allowed_actions"] for action in task.actions):
                reason = "action is outside agent allowlist"
            if reason:
                denied.append({**task.model_dump(mode="json"), "reason": reason})
            else:
                allowed.append(task.model_dump(mode="json"))
        await _record(trace_store, state, "policy.delegation.evaluated", "delegation_policy", {
            "allowed": len(allowed),
            "denied": denied,
        })
        return {
            "delegations": allowed,
            "denied_delegations": denied,
            "status": "authorized" if allowed else "denied",
        }

    def dispatch(state: EldercareState):
        return [
            Send(task["agent_id"], {
                "task_id": state.get("task_id", ""),
                "user_role": state.get("user_role", "system"),
                "event": state["event"],
                "delegation": task,
            })
            for task in state.get("delegations", [])
        ] or ["aggregate"]

    async def aggregate(state: EldercareState) -> dict[str, Any]:
        results = [ChildAgentResult.model_validate(item) for item in state.get("child_results", [])]
        denied = state.get("denied_delegations", [])
        succeeded = sum(item.status == "succeeded" for item in results)
        failed = len(results) - succeeded + len(denied)
        status = "succeeded" if succeeded and not failed else "partial_success" if succeeded else "denied"
        details = "；".join(item.summary for item in results if item.summary)
        response = f"养老院风险事件已处理：{succeeded} 个子智能体成功，{failed} 个失败或被拒绝。"
        if details:
            response += f" {details}"
        await _record(trace_store, state, "agent.node.completed", "butler", {
            "status": status,
            "child_results": [item.model_dump(mode="json") for item in results],
        })
        return {"status": status, "response": response}

    environment_graph = _build_child_graph(
        "environment_agent", action_service, trace_store=trace_store
    )
    care_graph = _build_child_graph("care_agent", action_service, trace_store=trace_store)

    builder = StateGraph(EldercareState)
    builder.add_node("butler_plan", butler_plan)
    builder.add_node("authorize", authorize)
    builder.add_node("dispatch", lambda _state: {})
    builder.add_node("environment_agent", environment_graph)
    builder.add_node("care_agent", care_graph)
    builder.add_node("aggregate", aggregate)
    builder.add_edge(START, "butler_plan")
    builder.add_edge("butler_plan", "authorize")
    builder.add_edge("authorize", "dispatch")
    builder.add_conditional_edges(
        "dispatch",
        dispatch,
        ["environment_agent", "care_agent", "aggregate"],
    )
    builder.add_edge("environment_agent", "aggregate")
    builder.add_edge("care_agent", "aggregate")
    builder.add_edge("aggregate", END)
    return builder.compile(checkpointer=checkpointer)


def _build_child_graph(
    agent_id: str,
    action_service: EldercareActionService,
    *,
    trace_store: InMemoryTraceStore | None,
):
    async def execute(state: EldercareState) -> dict[str, Any]:
        task = DelegatedTask.model_validate(state["delegation"])
        outputs: list[dict[str, Any]] = []
        for action in task.actions:
            handler = getattr(action_service, action.name)
            try:
                output = await handler(**action.arguments)
            except Exception as exc:
                output = {"success": False, "error": str(exc)}
            outputs.append({"action": action.name, "input": action.arguments, "output": output})
            await _record(trace_store, state, "agent.tool.completed", agent_id, {
                "tool": f"{task.allowed_mcp}.{action.name}",
                "delegation_id": task.delegation_id,
                "input": action.arguments,
                "output": output,
            })
        succeeded = sum(bool(item["output"].get("success")) for item in outputs)
        status = (
            "succeeded" if succeeded == len(outputs)
            else "partial_success" if succeeded
            else "failed"
        )
        result = ChildAgentResult(
            delegation_id=task.delegation_id,
            agent_id=agent_id,
            status=status,
            actions=outputs,
            summary=f"{agent_id} 完成 {succeeded}/{len(outputs)} 个动作。",
        )
        await _record(trace_store, state, "agent.subgraph.completed", agent_id, {
            "result": result.model_dump(mode="json"),
        })
        return {"child_results": [result.model_dump(mode="json")]}

    builder = StateGraph(EldercareState, output_schema=ChildOutput)
    builder.add_node("execute", execute)
    builder.add_edge(START, "execute")
    builder.add_edge("execute", END)
    return builder.compile()


async def _record(
    trace_store: InMemoryTraceStore | None,
    state: EldercareState,
    event_type: str,
    node: str,
    data: dict[str, Any] | None = None,
) -> None:
    task_id = state.get("task_id", "")
    if trace_store is None or not task_id:
        return
    await trace_store.record(
        task_id=task_id,
        type=event_type,
        node=node,
        data=data or {},
    )
