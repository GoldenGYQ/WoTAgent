"""Node and feature-unit tests for the LangGraph runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from wotagent.agent_graph import AgentGraphDependencies, build_agent_graph
from wotagent.agent_graph.graph import _agent_loop_context
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


def make_graph(plan: TaskPlan, gateway: FakeGateway, policy: PolicyEngine | None = None):
    async def planner(_state):
        return plan

    policy = policy or PolicyEngine()
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


def test_agent_loop_starts_with_structured_upstream_observations() -> None:
    upstream_observation = {
        "source_task_id": "task-observe",
        "step_id": "read-1",
        "kind": "query",
        "device_id": "light-001",
        "action": "read_state",
        "execution_status": "accepted",
        "result": {"on": False},
    }
    context = _agent_loop_context(
        {
            "message": "偶数时打开加湿器",
            "context": {
                "dependency_findings": [{
                    "task_id": "task-observe",
                    "agent_id": "device_ops_agent",
                    "objective": "统计开灯数量",
                    "status": "completed",
                    "finding": "开灯数量为 0",
                    "observations": [upstream_observation],
                }],
            },
            "execution_history": [],
            "agent_iterations": 0,
            "tool_call_count": 0,
        },
        SimpleNamespace(max_agent_iterations=4, max_tool_calls=16),
    )

    assert context["upstream_findings"][0]["task_id"] == "task-observe"
    assert context["observations"] == [upstream_observation]
    assert context["iteration"] == 1


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


def test_high_risk_plan_interrupts_before_device_call_and_resumes_once() -> None:
    gateway = FakeGateway()
    plan = TaskPlan(intent="control", summary="打开高风险测试灯", steps=[PlanStep(
        id="r1", kind="action", device_id="light-001", action="turnOn"
    )])
    graph = make_graph(plan, gateway, PolicyEngine(high_risk_actions={"turnOn"}))
    config = {"configurable": {"thread_id": "confirm-approved"}}

    interrupted = asyncio.run(graph.ainvoke(
        {"message": "打开灯", "role": "operator", "session_id": "confirm-session"},
        config=config,
    ))

    assert "__interrupt__" in interrupted
    assert interrupted["__interrupt__"][0].value["plan_id"] == plan.plan_id
    assert gateway.control_calls == 0

    resumed = asyncio.run(graph.ainvoke(Command(resume={"approved": True}), config=config))

    assert resumed["confirmation_approved"] is True
    assert resumed["trace"].status == TaskStatus.SUCCEEDED
    assert gateway.control_calls == 1


def test_rejected_confirmation_never_calls_device() -> None:
    gateway = FakeGateway()
    plan = TaskPlan(intent="control", summary="打开高风险测试灯", steps=[PlanStep(
        id="r1", kind="action", device_id="light-001", action="turnOn"
    )])
    graph = make_graph(plan, gateway, PolicyEngine(high_risk_actions={"turnOn"}))
    config = {"configurable": {"thread_id": "confirm-rejected"}}

    asyncio.run(graph.ainvoke(
        {"message": "打开灯", "role": "operator", "session_id": "confirm-session"},
        config=config,
    ))
    rejected = asyncio.run(graph.ainvoke(Command(resume={"approved": False}), config=config))

    assert rejected["confirmation_approved"] is False
    assert rejected["response"] == "操作已取消，未调用任何设备。"
    assert gateway.control_calls == 0


def test_verification_failure_can_replan_once_then_succeed() -> None:
    class EventuallyConsistentGateway(FakeGateway):
        async def control_device(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.control_calls += 1
            if self.control_calls >= 2:
                self.state["on"] = True
            return {"success": True}

    gateway = EventuallyConsistentGateway()
    plan = TaskPlan(intent="control", steps=[PlanStep(
        id="s1",
        kind="action",
        device_id="light-001",
        action="turnOn",
        postconditions=[Condition(property="on", value=True)],
    )])
    recoveries = 0

    async def planner(_state):
        return plan

    async def recovery_planner(state):
        nonlocal recoveries
        recoveries += 1
        assert state["trace"].status == TaskStatus.FAILED
        return plan.model_copy(deep=True)

    policy = PolicyEngine()
    graph = build_agent_graph(AgentGraphDependencies(
        planner=planner,
        policy=policy,
        orchestrator=TaskOrchestrator(gateway, policy),
        recovery_planner=recovery_planner,
        max_recovery_attempts=1,
    ))

    result = asyncio.run(graph.ainvoke({"message": "开灯", "role": "operator"}))

    assert result["trace"].status == TaskStatus.SUCCEEDED
    assert result["recovery_attempts"] == 1
    assert recoveries == 1
    assert gateway.control_calls == 2


def test_recovery_loop_never_exceeds_configured_bound() -> None:
    class NeverChangesGateway(FakeGateway):
        async def control_device(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.control_calls += 1
            return {"success": True}

    gateway = NeverChangesGateway()
    plan = TaskPlan(intent="control", steps=[PlanStep(
        id="s1",
        kind="action",
        device_id="light-001",
        action="turnOn",
        postconditions=[Condition(property="on", value=True)],
    )])

    async def planner(_state):
        return plan

    async def recovery_planner(_state):
        return plan.model_copy(deep=True)

    policy = PolicyEngine()
    graph = build_agent_graph(AgentGraphDependencies(
        planner=planner,
        policy=policy,
        orchestrator=TaskOrchestrator(gateway, policy),
        recovery_planner=recovery_planner,
        max_recovery_attempts=1,
    ))

    result = asyncio.run(graph.ainvoke({"message": "开灯", "role": "operator"}))

    assert result["trace"].status == TaskStatus.FAILED
    assert result["recovery_attempts"] == 1
    assert gateway.control_calls == 2


def test_response_agent_failure_falls_back_without_changing_trace_truth() -> None:
    gateway = FakeGateway()
    plan = TaskPlan(intent="control", steps=[PlanStep(
        id="s1",
        kind="action",
        device_id="light-001",
        action="turnOn",
        postconditions=[Condition(property="on", value=True)],
    )])

    async def planner(_state):
        return plan

    async def broken_responder(_state):
        raise RuntimeError("model unavailable")

    policy = PolicyEngine()
    graph = build_agent_graph(AgentGraphDependencies(
        planner=planner,
        policy=policy,
        orchestrator=TaskOrchestrator(gateway, policy),
        responder=broken_responder,
    ))

    result = asyncio.run(graph.ainvoke({"message": "开灯", "role": "operator"}))

    assert result["trace"].status == TaskStatus.SUCCEEDED
    assert result["response"] == "任务已执行并通过设备状态验证。"
    assert gateway.control_calls == 1


def test_observe_act_loop_replans_after_query_results_before_control() -> None:
    class ConditionalGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.states = {
                "light-001": {"on": True},
                "light-002": {"on": False},
                "ac-001": {"on": False},
            }
            self.actions: list[tuple[str, str]] = []

        async def query_state(self, device_id: str) -> dict[str, Any]:
            return dict(self.states[device_id])

        async def control_device(
            self,
            device_id: str,
            action: str,
            parameters: dict[str, Any],
            *,
            idempotency_key: str,
        ) -> dict[str, Any]:
            self.control_calls += 1
            self.actions.append((device_id, action))
            if action == "turnOn":
                self.states[device_id]["on"] = True
            return {"success": True}

    gateway = ConditionalGateway()
    planner_calls = 0

    async def planner(state):
        nonlocal planner_calls
        planner_calls += 1
        loop = state.get("context", {}).get("agent_loop", {})
        if planner_calls == 1:
            assert loop["observations"] == []
            return TaskPlan(intent="query", summary="读取灯状态", steps=[
                PlanStep(
                    id="read-light-1",
                    kind="query",
                    device_id="light-001",
                    action="queryState",
                ),
                PlanStep(
                    id="read-light-2",
                    kind="query",
                    device_id="light-002",
                    action="queryState",
                ),
            ])
        if planner_calls == 2:
            observations = loop["observations"]
            assert [item["result"]["on"] for item in observations] == [True, False]
            return TaskPlan(intent="control", summary="开灯数为奇数，打开空调", steps=[
                PlanStep(
                    id="turn-on-ac",
                    kind="action",
                    device_id="ac-001",
                    action="turnOn",
                ),
            ])
        assert loop["observations"][-1]["verification"]["passed"] is True
        return TaskPlan(intent="chat", summary="条件分支已执行并验证")

    policy = PolicyEngine()
    graph = build_agent_graph(AgentGraphDependencies(
        planner=planner,
        policy=policy,
        orchestrator=TaskOrchestrator(gateway, policy),
        max_agent_iterations=4,
        max_tool_calls=8,
    ))

    result = asyncio.run(graph.ainvoke({
        "message": "检查灯的状态，开灯数为奇数时打开空调",
        "role": "operator",
        "context": {
            "execution_mode": "observe_act_loop",
            "original_message": "检查灯的状态，开灯数为奇数时打开空调",
        },
    }))

    assert planner_calls == 3
    assert result["agent_iterations"] == 2
    assert result["tool_call_count"] == 3
    assert len(result["execution_history"]) == 2
    assert gateway.actions == [("ac-001", "turnOn")]
    assert result["response"] == "条件分支已执行并验证"


def test_observe_act_loop_stops_at_iteration_limit() -> None:
    gateway = FakeGateway()
    planner_calls = 0

    async def planner(_state):
        nonlocal planner_calls
        planner_calls += 1
        return TaskPlan(intent="query", summary="读取状态", steps=[
            PlanStep(
                id="read-light",
                kind="query",
                device_id="light-001",
                action="queryState",
            ),
        ])

    policy = PolicyEngine()
    graph = build_agent_graph(AgentGraphDependencies(
        planner=planner,
        policy=policy,
        orchestrator=TaskOrchestrator(gateway, policy),
        max_agent_iterations=1,
        max_tool_calls=8,
    ))

    result = asyncio.run(graph.ainvoke({
        "message": "根据灯状态继续处理",
        "role": "operator",
        "context": {"execution_mode": "observe_act_loop"},
    }))

    assert planner_calls == 1
    assert result["phase"] == "responded"
    assert "maximum iteration limit" in result["error"]
    assert result["agent_iterations"] == 1
    assert result["tool_call_count"] == 1
