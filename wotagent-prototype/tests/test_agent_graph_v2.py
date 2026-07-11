"""Node and feature-unit tests for the LangGraph runtime."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from wotagent.agent_graph import AgentGraphDependencies, build_agent_graph
from wotagent.domain import Condition, PlanStep, TaskPlan, TaskStatus
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine


class FakeGateway:
    name = "fake"

    def __init__(self) -> None:
        self.state = {"on": False}
        self.control_calls = 0

    async def list_devices(self, location: str | None = None) -> list[dict[str, Any]]:
        return []

    async def query_state(self, device_id: str) -> dict[str, Any]:
        return dict(self.state)

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.control_calls += 1
        self.state["on"] = True
        return {"success": True}


def make_graph(plan: TaskPlan, gateway: FakeGateway):
    async def planner(_state):
        return plan

    policy = PolicyEngine()
    return build_agent_graph(
        AgentGraphDependencies(
            planner=planner,
            policy=policy,
            orchestrator=TaskOrchestrator(gateway, policy),
        ),
        checkpointer=InMemorySaver(),
    )


def invoke(graph, role: str = "operator"):
    return asyncio.run(graph.ainvoke(
        {"message": "打开客厅灯", "role": role, "session_id": "test-session"},
        config={"configurable": {"thread_id": f"thread-{role}"}},
    ))


def test_full_control_path_plans_authorizes_executes_and_responds() -> None:
    gateway = FakeGateway()
    plan = TaskPlan(intent="control", steps=[PlanStep(
        id="s1",
        kind="action",
        device_id="light-001",
        action="turnOn",
        postconditions=[Condition(property="on", value=True)],
    )])

    result = invoke(make_graph(plan, gateway))

    assert result["phase"] == "responded"
    assert result["trace"].status == TaskStatus.SUCCEEDED
    assert result["policy_decisions"][0].effect == "allow"
    assert gateway.control_calls == 1
    assert "状态验证" in result["response"]


def test_policy_path_stops_viewer_before_executor() -> None:
    gateway = FakeGateway()
    plan = TaskPlan(intent="control", steps=[PlanStep(
        id="s1", kind="action", device_id="light-001", action="turnOn"
    )])

    result = invoke(make_graph(plan, gateway), role="viewer")

    assert result["policy_decisions"][0].effect == "deny"
    assert "trace" not in result
    assert gateway.control_calls == 0
    assert "未执行" in result["response"]


def test_chat_path_skips_policy_and_device_execution() -> None:
    gateway = FakeGateway()
    result = invoke(make_graph(TaskPlan(intent="chat", summary="你好"), gateway))

    assert result["response"] == "你好"
    assert "policy_decisions" not in result
    assert "trace" not in result
    assert gateway.control_calls == 0


def test_individual_policy_node_can_be_tested_in_isolation() -> None:
    gateway = FakeGateway()
    plan = TaskPlan(intent="query", steps=[PlanStep(
        id="q1", kind="query", device_id="light-001", action="queryState"
    )])
    graph = make_graph(plan, gateway)

    result = asyncio.run(graph.nodes["policy"].ainvoke({"plan": plan, "role": "viewer"}))

    assert result["phase"] == "policy_checked"
    assert result["policy_decisions"][0].effect == "allow"
