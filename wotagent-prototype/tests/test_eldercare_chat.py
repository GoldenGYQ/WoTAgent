from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage

from wotagent.domains.eldercare import (
    EldercareChatAgent,
    EldercareCommandPlanner,
    EldercareRuntime,
    EldercareStore,
    Resident,
    SensorReading,
    is_eldercare_request,
)
from wotagent.domains.eldercare.models import AgentFinding, Evidence
from wotagent.telemetry import InMemoryTraceStore


class FakePlannerRunnable:
    def __init__(self, arguments: dict) -> None:
        self.arguments = arguments

    async def ainvoke(self, _messages):
        return AIMessage(content="", tool_calls=[{
            "name": "generate_eldercare_plan",
            "args": self.arguments,
            "id": "call-eldercare",
            "type": "tool_call",
        }])


class FakeThinkingRunnable:
    async def ainvoke(self, _messages):
        return AIMessage(content="目标居民是张爷爷，需要查询健康摘要。")


class FakeEnvironmentGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.target = 25.0

    async def control_device(self, device_id, action, parameters, *, idempotency_key):
        self.calls.append((device_id, action, parameters))
        self.target = float(parameters["temperature"])
        return {"success": True, "idempotency_key": idempotency_key}

    async def query_state(self, device_id):
        return {"device_id": device_id, "targetTemp": self.target}


def make_agent(tmp_path, plan: dict, gateway=None):
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        caregiver="王护理员",
        environment_device_id="ac-002",
    ))
    store.add_reading(SensorReading(
        resident_id="elderly-001", sensor_type="heart_rate", value=82, unit="bpm"
    ))
    trace = InMemoryTraceStore()
    runtime = EldercareRuntime(
        store,
        trace_store=trace,
        environment_gateway=gateway,
    )
    planner = EldercareCommandPlanner(FakePlannerRunnable(plan), trace_store=trace)
    return EldercareChatAgent(runtime, planner, trace_store=trace), trace


def test_router_selects_eldercare_and_preserves_ordinary_wot() -> None:
    residents = [Resident(resident_id="elderly-001", name="张爷爷", room="A-101")]

    assert is_eldercare_request("查看张爷爷今天的状态", [], residents)
    assert is_eldercare_request("血氧现在怎么样", [], residents)
    assert not is_eldercare_request("把客厅灯关掉", [], residents)


def test_chat_query_reads_health_summary_through_mcp_resource(tmp_path) -> None:
    agent, trace = make_agent(tmp_path, {
        "intent": "query",
        "resident_id": "elderly-001",
        "summary": "查询健康状态",
        "actions": [{"name": "read_health_summary"}],
    })

    async def run():
        await trace.create(task_id="task-query", session_id="conv", request_id="req")
        result = await agent.run(
            task_id="task-query",
            message="查看张爷爷今天的状态",
            history=[],
            role="viewer",
        )
        return result, await trace.events("task-query")

    result, events = asyncio.run(run())
    assert result["status"] == "succeeded"
    assert "心率82bpm" in result["response"]
    assert any(event.type == "agent.resource.completed" for event in events)
    assert any(event.node == "eldercare_planner" for event in events)


def test_operator_temperature_command_runs_environment_subgraph_and_wot_gateway(tmp_path) -> None:
    gateway = FakeEnvironmentGateway()
    agent, trace = make_agent(tmp_path, {
        "intent": "control",
        "resident_id": "elderly-001",
        "summary": "调节张爷爷房间温度",
        "actions": [{"name": "adjust_temperature", "target_temperature": 24}],
    }, gateway=gateway)

    async def run():
        await trace.create(task_id="task-control", session_id="conv", request_id="req")
        return await agent.run(
            task_id="task-control",
            message="把张爷爷房间调到24度",
            history=[],
            role="operator",
        )

    result = asyncio.run(run())
    assert result["status"] == "succeeded"
    assert gateway.calls == [("ac-002", "setTemperature", {"temperature": 24.0})]
    assert "WoT/MCP" in result["response"]


def test_viewer_cannot_execute_eldercare_action(tmp_path) -> None:
    gateway = FakeEnvironmentGateway()
    agent, trace = make_agent(tmp_path, {
        "intent": "control",
        "resident_id": "elderly-001",
        "summary": "调温",
        "actions": [{"name": "adjust_temperature", "target_temperature": 24}],
    }, gateway=gateway)

    async def run():
        await trace.create(task_id="task-denied", session_id="conv", request_id="req")
        return await agent.run(
            task_id="task-denied",
            message="把张爷爷房间调到24度",
            history=[],
            role="viewer",
        )

    result = asyncio.run(run())
    assert result["status"] == "denied"
    assert gateway.calls == []


def test_eldercare_planner_honors_thinking_mode(tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    resident = store.upsert_resident(Resident(
        resident_id="elderly-001", name="张爷爷", room="A-101"
    ))
    trace = InMemoryTraceStore()
    planner = EldercareCommandPlanner(
        FakePlannerRunnable({
            "intent": "query",
            "resident_id": "elderly-001",
            "actions": [{"name": "read_health_summary"}],
        }),
        trace_store=trace,
        thinking_runnable=FakeThinkingRunnable(),
    )

    async def run():
        await trace.create(task_id="task-thinking", session_id="conv", request_id="req")
        plan = await planner.plan(
            task_id="task-thinking",
            message="深入分析张爷爷今天的状态",
            history=[],
            residents=[resident],
            resolved_resident=resident,
            thinking=True,
        )
        return plan, await trace.events("task-thinking")

    plan, events = asyncio.run(run())
    assert plan.intent == "query"
    completed = [event for event in events if event.type == "model.request.completed"]
    assert [event.node for event in completed] == [
        "eldercare_reasoner",
        "eldercare_planner",
    ]


def test_health_route_uses_bounded_health_agent_loop(tmp_path) -> None:
    agent, trace = make_agent(tmp_path, {
        "intent": "clarify",
        "clarification": "planner should not be used",
    })

    class FakeHealthLoop:
        async def run(self, *, task_id, resident_id, delegation):
            assert resident_id == "elderly-001"
            assert "趋势" in delegation.objective
            return AgentFinding(
                agent="health_agent",
                objective=delegation.objective,
                status="completed",
                finding="最近三次血氧为94%、92%、90%，呈下降趋势，需要护理人员复核。",
                confidence=0.94,
                evidence=[Evidence(
                    source="read_vital_trend",
                    value={"spo2": [94, 92, 90]},
                    reliability=1.0,
                    description="最近血氧趋势",
                )],
                recommended_actions=["通知护理员复核"],
            )

    agent.runtime.health_agent = FakeHealthLoop()

    async def run():
        await trace.create(task_id="task-trend", session_id="conv", request_id="req")
        result = await agent.run(
            task_id="task-trend",
            message="查询张爷爷最近的健康趋势",
            history=[],
            role="viewer",
            routed_agent="health_agent",
            resident_id="elderly-001",
        )
        return result, await trace.events("task-trend")

    result, events = asyncio.run(run())
    assert result["status"] == "succeeded"
    assert "下降趋势" in result["response"]
    assert result["finding"]["evidence"][0]["source"] == "read_vital_trend"
    assert any(event.type == "agent.finding.completed" for event in events)
