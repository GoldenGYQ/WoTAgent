import asyncio

from langchain_core.messages import AIMessage

from wotagent.eldercare import Resident
from wotagent.supervisor import (
    AGENT_REGISTRY,
    StructuredSupervisorRouter,
    select_supervisor_route,
)


RESIDENTS = [
    Resident(resident_id="elderly-001", name="张爷爷", room="A-101"),
]


def test_supervisor_routes_resident_health_to_health_agent() -> None:
    route = select_supervisor_route("查看张爷爷今天的健康状态", [], RESIDENTS)

    assert route.runtime == "eldercare"
    assert route.agent_id == "health_agent"


def test_supervisor_routes_caregiver_request_to_care_agent() -> None:
    route = select_supervisor_route("通知张爷爷的护理员尽快查看", [], RESIDENTS)

    assert route.runtime == "eldercare"
    assert route.agent_id == "care_agent"


def test_supervisor_routes_villa_domains_to_td_device_agents() -> None:
    assert select_supervisor_route("检查厨房燃气报警", [], RESIDENTS).agent_id == "security_agent"
    assert select_supervisor_route("统计今天的用电量", [], RESIDENTS).agent_id == "energy_agent"
    assert select_supervisor_route("让扫地机器人清洁一楼", [], RESIDENTS).agent_id == "housekeeping_agent"
    assert select_supervisor_route("检查哪些设备离线", [], RESIDENTS).agent_id == "device_ops_agent"
    assert select_supervisor_route("把客厅灯调暗", [], RESIDENTS).agent_id == "environment_agent"


def test_registry_exposes_all_butler_domains() -> None:
    assert set(AGENT_REGISTRY) == {
        "health_agent",
        "care_agent",
        "environment_agent",
        "security_agent",
        "energy_agent",
        "housekeeping_agent",
        "device_ops_agent",
    }
    assert AGENT_REGISTRY["security_agent"].mcp_scopes == ("wot.security",)
    assert AGENT_REGISTRY["energy_agent"].runtime == "langgraph_subgraph"


def test_structured_supervisor_resolves_elliptical_health_follow_up() -> None:
    class FakeRouteRunnable:
        async def ainvoke(self, messages):
            assert any("张爷爷" in str(item.content) for item in messages)
            return AIMessage(content="", tool_calls=[{
                "name": "select_agent_route",
                "args": {
                    "action": "delegate",
                    "agent_id": "health_agent",
                    "resolved_request": "查询张爷爷最近的健康趋势",
                    "resident_id": "elderly-001",
                    "confidence": 0.97,
                    "reason": "继承上一轮张爷爷健康状态查询",
                },
                "id": "route-1",
                "type": "tool_call",
            }])

    decision = asyncio.run(StructuredSupervisorRouter(
        FakeRouteRunnable(),
    ).route(
        task_id="",
        message="那最近的趋势呢",
        history=[
            {"role": "user", "content": "查看张爷爷今天的健康状态"},
            {"role": "assistant", "content": "张爷爷当前心率82。"},
        ],
        residents=RESIDENTS,
        runtime_context={"last_task": {"user_message": "查看张爷爷今天的健康状态"}},
    ))

    assert decision.agent_id == "health_agent"
    assert decision.resident_id == "elderly-001"
    assert decision.resolved_request == "查询张爷爷最近的健康趋势"


def test_structured_supervisor_marks_state_dependent_automation_as_loop() -> None:
    class FakeRouteRunnable:
        async def ainvoke(self, messages):
            system = str(messages[0].content)
            assert "observe_act_loop" in system
            assert "Never use device_ops_agent for ordinary" in system
            return AIMessage(content="", tool_calls=[{
                "name": "select_agent_route",
                "args": {
                    "action": "delegate",
                    "agent_id": "environment_agent",
                    "resolved_request": "先统计开灯数量，再按奇偶控制空调或加湿器",
                    "execution_mode": "observe_act_loop",
                    "confidence": 0.98,
                    "reason": "后续动作取决于设备查询结果",
                },
                "id": "route-loop",
                "type": "tool_call",
            }])

    decision = asyncio.run(StructuredSupervisorRouter(
        FakeRouteRunnable(),
    ).route(
        task_id="",
        message="检查灯的状态，如果开着的灯是奇数就打开空调，否则打开加湿器",
        history=[],
        residents=RESIDENTS,
        runtime_context={},
    ))

    assert decision.agent_id == "environment_agent"
    assert decision.execution_mode == "observe_act_loop"
