from __future__ import annotations

import asyncio
from typing import Any

from wotagent.domains.eldercare.health_agent import HealthAgentLoop
from wotagent.domains.eldercare.health_gateway import ScopedHealthMCPClient
from wotagent.domains.eldercare import EldercareStore
from wotagent.domains.eldercare.models import (
    Delegation,
    HealthAgentDecision,
    ToolExecutionResult,
)


class FakeHealthGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def available_tools(self) -> list[str]:
        return [
            "read_health_summary",
            "read_latest_vitals",
            "read_vital_trend",
            "check_data_quality",
        ]

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        self.calls.append(tool_name)
        payloads = {
            "read_health_summary": {
                "latest": {
                    "spo2": {"value": 89, "observed_at": "2026-07-16T00:00:00Z"},
                },
            },
            "check_data_quality": {
                "sensors": {"spo2": {"fresh": False, "age_seconds": 900}},
            },
            "read_latest_vitals": {
                "spo2": {"value": 89, "observed_at": "2026-07-16T00:15:00Z"},
            },
            "read_vital_trend": {
                "spo2": [{"value": 91}, {"value": 90}, {"value": 89}],
            },
        }
        return ToolExecutionResult(
            success=True,
            verified=True,
            tool_name=tool_name,
            arguments=arguments,
            result=payloads[tool_name],
            observed_state=payloads[tool_name],
        )


class EvidenceAwareFakeLLM:
    async def __call__(self, context: dict[str, Any]) -> HealthAgentDecision:
        sources = {item["source"] for item in context["evidence"]}
        if "read_health_summary" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="read_health_summary",
                reason="先读取摘要",
            )
        if "check_data_quality" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="check_data_quality",
                reason="摘要时间较旧，需要检查数据质量",
            )
        if "read_latest_vitals" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="read_latest_vitals",
                reason="确认摘要过期，主动读取最新值",
            )
        if "read_vital_trend" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="read_vital_trend",
                reason="最新血氧仍异常，继续查询趋势",
            )
        return HealthAgentDecision(
            action="finish",
            reason="趋势证据充分",
            status="completed",
            finding="血氧持续偏低，需要护理人员立即复核。",
            confidence=0.95,
            recommended_actions=["通知护理员"],
        )


def delegation() -> Delegation:
    return Delegation(
        agent="health_agent",
        objective="核实低血氧风险",
        reason="Monitor 触发低血氧硬告警",
        expected_evidence=["最新血氧", "趋势"],
    )


def test_health_agent_uses_staleness_and_latest_value_to_choose_next_tools() -> None:
    gateway = FakeHealthGateway()
    finding = asyncio.run(HealthAgentLoop(
        gateway,
        EvidenceAwareFakeLLM(),
        max_iterations=5,
    ).run(
        task_id="",
        resident_id="elderly-001",
        delegation=delegation(),
    ))

    assert gateway.calls == [
        "read_health_summary",
        "check_data_quality",
        "read_latest_vitals",
        "read_vital_trend",
    ]
    assert finding.status == "completed"
    assert finding.confidence == 0.95
    assert len(finding.evidence) == 4


def test_health_agent_stops_safely_at_max_iterations() -> None:
    class EndlessFakeLLM:
        async def __call__(self, context):
            source = (
                "read_health_summary"
                if not context["evidence"]
                else "check_data_quality"
            )
            return HealthAgentDecision(
                action="use_tool",
                tool_name=source,
                reason="继续调查",
            )

    finding = asyncio.run(HealthAgentLoop(
        FakeHealthGateway(),
        EndlessFakeLLM(),
        max_iterations=2,
    ).run(
        task_id="",
        resident_id="elderly-001",
        delegation=delegation(),
    ))

    assert finding.status == "needs_more_information"
    assert "最大调查轮次" in finding.finding
    assert len(finding.tool_calls) == 2


def test_health_agent_rejects_repeated_plan() -> None:
    class RepeatingFakeLLM:
        async def __call__(self, _context):
            return HealthAgentDecision(
                action="use_tool",
                tool_name="read_health_summary",
                reason="重复读取",
            )

    finding = asyncio.run(HealthAgentLoop(
        FakeHealthGateway(),
        RepeatingFakeLLM(),
        max_iterations=4,
    ).run(
        task_id="",
        resident_id="elderly-001",
        delegation=delegation(),
    ))

    assert finding.status == "needs_more_information"
    assert "重复健康调查计划" in finding.finding
    assert len(finding.tool_calls) == 1


def test_scoped_health_mcp_client_blocks_unregistered_resource(tmp_path) -> None:
    gateway = ScopedHealthMCPClient(
        EldercareStore(tmp_path / "eldercare.db"),
        allowed_tools={"read_health_summary"},
    )
    result = asyncio.run(gateway.call(
        "read_vital_trend",
        {"resident_id": "elderly-001"},
    ))

    assert result.success is False
    assert result.error_code == "MCP_SCOPE_DENIED"
