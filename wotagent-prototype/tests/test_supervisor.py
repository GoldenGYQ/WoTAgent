from wotagent.eldercare import Resident
from wotagent.supervisor import AGENT_REGISTRY, select_supervisor_route


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
