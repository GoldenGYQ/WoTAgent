"""Bounded Observe-Reason-Act loop for health investigation."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..telemetry import InMemoryTraceStore
from .health_gateway import ScopedHealthMCPClient
from .models import (
    AgentFinding,
    Delegation,
    Evidence,
    HealthAgentDecision,
    ToolExecutionResult,
)


HealthDecisionMaker = Callable[
    [dict[str, Any]],
    HealthAgentDecision | Awaitable[HealthAgentDecision],
]


class HealthAgentLoop:
    def __init__(
        self,
        gateway: ScopedHealthMCPClient,
        decision_maker: HealthDecisionMaker,
        *,
        trace_store: InMemoryTraceStore | None = None,
        max_iterations: int = 4,
        max_tool_calls: int = 8,
        fallback_decision_maker: HealthDecisionMaker | None = None,
    ) -> None:
        self.gateway = gateway
        self.decision_maker = decision_maker
        self.trace_store = trace_store
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.fallback_decision_maker = fallback_decision_maker

    async def run(
        self,
        *,
        task_id: str,
        resident_id: str,
        delegation: Delegation,
    ) -> AgentFinding:
        evidence: list[Evidence] = []
        tool_calls: list[ToolExecutionResult] = []
        call_signatures: set[str] = set()
        for iteration in range(1, self.max_iterations + 1):
            context = {
                "task_id": task_id,
                "agent": "health_agent",
                "objective": delegation.objective,
                "resident_id": resident_id,
                "expected_evidence": delegation.expected_evidence,
                "available_tools": self.gateway.available_tools(),
                "evidence": [item.model_dump(mode="json") for item in evidence],
                "tool_calls": [item.model_dump(mode="json") for item in tool_calls],
                "iteration": iteration,
                "max_iterations": self.max_iterations,
                "remaining_tool_calls": self.max_tool_calls - len(tool_calls),
            }
            try:
                decision = await _decide(self.decision_maker, context)
            except Exception as exc:
                if self.fallback_decision_maker is None:
                    raise
                await self._record(task_id, "agent.fallback.activated", iteration, {
                    "component": "health_decision",
                    "reason": str(exc),
                })
                decision = await _decide(self.fallback_decision_maker, context)
            await self._record(task_id, "agent.health.decision", iteration, {
                "objective": delegation.objective,
                "evidence_count": len(evidence),
                "action": decision.action,
                "tool": decision.tool_name,
                "reason": decision.reason,
                "continue": decision.action == "use_tool",
            })
            if decision.action == "finish":
                status = decision.status
                if not evidence and status == "completed":
                    status = "needs_more_information"
                return AgentFinding(
                    agent="health_agent",
                    objective=delegation.objective,
                    status=status,
                    finding=decision.finding,
                    confidence=decision.confidence,
                    evidence=evidence,
                    recommended_actions=decision.recommended_actions,
                    unresolved_questions=decision.unresolved_questions,
                    tool_calls=tool_calls,
                )
            if len(tool_calls) >= self.max_tool_calls:
                break
            arguments = {**decision.arguments, "resident_id": resident_id}
            signature = json.dumps(
                {"tool": decision.tool_name, "arguments": arguments},
                ensure_ascii=False,
                sort_keys=True,
            )
            if signature in call_signatures:
                return self._bounded_finding(
                    delegation,
                    evidence,
                    tool_calls,
                    "检测到重复健康调查计划，已安全终止。",
                    ["需要人工选择新的调查方向"],
                )
            call_signatures.add(signature)
            result = await self.gateway.call(str(decision.tool_name), arguments)
            tool_calls.append(result)
            await self._record(task_id, "agent.resource.completed", iteration, {
                "tool": decision.tool_name,
                "input": arguments,
                "output": result.model_dump(mode="json"),
                "reason": decision.reason,
            })
            evidence.append(Evidence(
                source=str(decision.tool_name),
                value=result.result if result.success else {
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                },
                timestamp=_evidence_timestamp(result),
                reliability=1.0 if result.success and result.verified else 0.0,
                description=(
                    f"{decision.tool_name} 返回了已验证的健康资源"
                    if result.success
                    else f"{decision.tool_name} 调用失败：{result.error_message}"
                ),
            ))
        return self._bounded_finding(
            delegation,
            evidence,
            tool_calls,
            "Health Agent 达到最大调查轮次，当前证据仍不足以完成目标。",
            ["需要护理人员复核或在新数据到达后重新调查"],
        )

    @staticmethod
    def _bounded_finding(
        delegation: Delegation,
        evidence: list[Evidence],
        tool_calls: list[ToolExecutionResult],
        finding: str,
        unresolved: list[str],
    ) -> AgentFinding:
        return AgentFinding(
            agent="health_agent",
            objective=delegation.objective,
            status="needs_more_information",
            finding=finding,
            confidence=0.4 if evidence else 0.1,
            evidence=evidence,
            recommended_actions=["请求护理人员复核"],
            unresolved_questions=unresolved,
            tool_calls=tool_calls,
        )

    async def _record(
        self,
        task_id: str,
        event_type: str,
        iteration: int,
        data: dict[str, Any],
    ) -> None:
        if self.trace_store is None or not task_id:
            return
        await self.trace_store.record(
            task_id=task_id,
            type=event_type,
            node="health_agent",
            iteration=iteration,
            data=data,
        )


def _evidence_timestamp(result: ToolExecutionResult) -> str | None:
    payload = result.result
    if not isinstance(payload, dict):
        return None
    for key in ("generated_at", "checked_at", "observed_at"):
        if payload.get(key):
            return str(payload[key])
    latest = payload.get("latest")
    if isinstance(latest, dict):
        timestamps = [
            str(item["observed_at"])
            for item in latest.values()
            if isinstance(item, dict) and item.get("observed_at")
        ]
        return max(timestamps) if timestamps else None
    return None


async def _decide(
    decision_maker: HealthDecisionMaker,
    context: dict[str, Any],
) -> HealthAgentDecision:
    value = decision_maker(context)
    resolved = await value if hasattr(value, "__await__") else value
    return HealthAgentDecision.model_validate(resolved)


__all__ = ["HealthAgentLoop", "HealthDecisionMaker"]
