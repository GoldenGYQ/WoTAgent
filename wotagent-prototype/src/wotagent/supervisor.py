"""Unified Butler routing across eldercare and villa automation domains."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from .eldercare.chat import is_eldercare_request
from .eldercare.models import Resident


AgentId = Literal[
    "health_agent",
    "care_agent",
    "environment_agent",
    "security_agent",
    "energy_agent",
    "housekeeping_agent",
    "device_ops_agent",
]


@dataclass(frozen=True)
class DomainAgentSpec:
    agent_id: AgentId
    domain: str
    responsibility: str
    runtime: Literal["eldercare_subgraph", "langgraph_subgraph"]
    mcp_scopes: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mcp_scopes"] = list(self.mcp_scopes)
        return payload


AGENT_REGISTRY: dict[AgentId, DomainAgentSpec] = {
    "health_agent": DomainAgentSpec(
        "health_agent",
        "health",
        "读取健康资源、解释生命体征并接收健康风险事件",
        "eldercare_subgraph",
        ("wot.health", "eldercare-health"),
    ),
    "care_agent": DomainAgentSpec(
        "care_agent",
        "care",
        "通知护理员并创建照护任务",
        "eldercare_subgraph",
        ("eldercare-care",),
    ),
    "environment_agent": DomainAgentSpec(
        "environment_agent",
        "environment",
        "控制灯光、空调、空气、窗帘和其他环境设备",
        "langgraph_subgraph",
        ("wot.environment",),
    ),
    "security_agent": DomainAgentSpec(
        "security_agent",
        "security",
        "处理门禁、燃气、烟雾、摄像头和应急联动",
        "langgraph_subgraph",
        ("wot.security",),
    ),
    "energy_agent": DomainAgentSpec(
        "energy_agent",
        "energy",
        "读取能耗设备并执行受限负载调度",
        "langgraph_subgraph",
        ("wot.energy",),
    ),
    "housekeeping_agent": DomainAgentSpec(
        "housekeeping_agent",
        "housekeeping",
        "控制清洁和家务设备",
        "langgraph_subgraph",
        ("wot.housekeeping",),
    ),
    "device_ops_agent": DomainAgentSpec(
        "device_ops_agent",
        "device_ops",
        "发现 TD、查询设备状态并诊断接入异常",
        "langgraph_subgraph",
        ("wot.device_ops",),
    ),
}


@dataclass(frozen=True)
class SupervisorRoute:
    agent_id: AgentId
    runtime: Literal["eldercare", "device"]
    reason: str


def select_supervisor_route(
    message: str,
    history: list[dict[str, Any]],
    residents: list[Resident],
) -> SupervisorRoute:
    text = message.lower()
    if is_eldercare_request(message, history, residents):
        care_terms = ("通知", "联系", "护理员", "照护任务", "安排复测", "创建任务")
        agent_id: AgentId = (
            "care_agent" if any(term in text for term in care_terms) else "health_agent"
        )
        return SupervisorRoute(agent_id, "eldercare", "resident or health context matched")

    domain_terms: tuple[tuple[AgentId, tuple[str, ...]], ...] = (
        ("security_agent", ("门锁", "门禁", "摄像头", "燃气", "烟雾", "报警", "安防")),
        ("energy_agent", ("能耗", "用电", "电表", "电费", "负载", "充电")),
        ("housekeeping_agent", ("扫地", "清洁", "拖地", "家务", "机器人")),
        ("device_ops_agent", ("离线", "故障", "设备状态", "td", "mcp", "网关", "接入")),
    )
    for agent_id, terms in domain_terms:
        if any(term in text for term in terms):
            return SupervisorRoute(agent_id, "device", f"{agent_id} domain terms matched")
    return SupervisorRoute(
        "environment_agent",
        "device",
        "default TD-described villa automation domain",
    )


__all__ = ["AGENT_REGISTRY", "select_supervisor_route"]
