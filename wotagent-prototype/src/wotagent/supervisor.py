"""Structured Butler routing across eldercare and villa automation domains."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from .agents import AGENT_SPECS, AgentSpec
from .domains.eldercare.models import Resident
from .prompting import compact_history, context_message
from .telemetry import InMemoryTraceStore, extract_usage_with_cost


AgentId = Literal[
    "health_agent",
    "care_agent",
    "environment_agent",
    "security_agent",
    "energy_agent",
    "housekeeping_agent",
    "device_ops_agent",
]


DomainAgentSpec = AgentSpec
AGENT_REGISTRY: dict[str, AgentSpec] = AGENT_SPECS


class RouteDecision(BaseModel):
    action: Literal["delegate", "clarify"] = "delegate"
    agent_id: AgentId | None = None
    resolved_request: str = Field(min_length=1)
    execution_mode: Literal["single_pass", "observe_act_loop"] = "single_pass"
    resident_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(min_length=1)
    clarification: str = ""

    @model_validator(mode="after")
    def validate_route(self) -> "RouteDecision":
        if self.action == "delegate" and self.agent_id is None:
            raise ValueError("agent_id is required when action=delegate")
        if self.action == "clarify" and not self.clarification:
            raise ValueError("clarification is required when action=clarify")
        return self

    @property
    def runtime(self) -> Literal["eldercare", "device"]:
        if self.agent_id in {"health_agent", "care_agent"}:
            return "eldercare"
        return "device"


ROUTE_DECISION_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "select_agent_route",
        "description": "Resolve the user's request and select one authorized professional agent",
        "parameters": RouteDecision.model_json_schema(),
    },
}


class StructuredSupervisorRouter:
    """LLM Butler router with validated output and deterministic fallback."""

    def __init__(
        self,
        runnable: Any,
        *,
        timeout_seconds: float = 12,
        trace_store: InMemoryTraceStore | None = None,
    ) -> None:
        self.runnable = runnable
        self.timeout_seconds = timeout_seconds
        self.trace_store = trace_store

    async def route(
        self,
        *,
        task_id: str,
        message: str,
        history: list[dict[str, Any]],
        residents: list[Resident],
        runtime_context: dict[str, Any],
        attempted_agents: list[str] | None = None,
        previous_outcome: dict[str, Any] | None = None,
    ) -> RouteDecision:
        attempted = attempted_agents or []
        context = {
            "available_agents": [
                spec.model_dump(mode="json") for spec in AGENT_REGISTRY.values()
                if spec.agent_id not in attempted
            ],
            "residents": [item.model_dump(mode="json") for item in residents],
            "runtime_context": runtime_context,
            "attempted_agents": attempted,
            "previous_outcome": previous_outcome,
        }
        messages: list[Any] = [
            SystemMessage(content=(
                "You are the WoTAgent Butler Supervisor. Resolve elliptical references using "
                "conversation history and structured runtime context, then delegate to exactly "
                "one professional agent. Select by objective and required evidence, not keyword "
                "matching. health_agent investigates health state, freshness and trends; "
                "care_agent performs caregiver coordination; environment_agent handles comfort "
                "devices, routine device-state inspection, and conditional automation across "
                "lights, climate, air and curtains; security_agent handles safety devices; "
                "energy_agent handles energy; "
                "housekeeping_agent handles cleaning; device_ops_agent diagnoses TD/MCP/device "
                "integration faults such as offline devices, missing capabilities, gateway "
                "errors or undeclared affordances. Never use device_ops_agent for ordinary "
                "state-dependent home automation. Set execution_mode=observe_act_loop when later "
                "actions depend on values, counts, comparisons, branches, or results that must "
                "first be observed. Use single_pass only when the complete safe action is already "
                "known before tool execution. If a previous specialist returned a structured clarify or "
                "out-of-scope outcome, reconsider the domain and choose another unattempted "
                "agent. Clarify only when the target or objective truly cannot be resolved. "
                "Return only the select_agent_route tool call."
            )),
            HumanMessage(content=context_message("supervisor_context", context)),
        ]
        for item in compact_history(history, max_messages=8, max_characters=500):
            message_type = HumanMessage if item["role"] == "user" else AIMessage
            messages.append(message_type(content=(
                f"Conversation reference ({item['role']}): {item['content']}"
            )))
        messages.append(HumanMessage(content=message))
        await self._record(task_id, "model.request.started", {
            "purpose": "supervisor_routing",
            "prompt_version": "supervisor-v2",
            "attempted_agents": attempted,
        })
        try:
            result = await asyncio.wait_for(
                self.runnable.ainvoke(messages),
                timeout=self.timeout_seconds,
            )
            decision = RouteDecision.model_validate(_tool_arguments(result))
            if decision.agent_id in attempted:
                raise ValueError(f"repeated route to {decision.agent_id}")
            await self._record(task_id, "model.request.completed", {
                "purpose": "supervisor_routing",
                "prompt_version": "supervisor-v2",
                "agent_id": decision.agent_id,
                "confidence": decision.confidence,
                **extract_usage_with_cost(result),
            })
            return decision
        except Exception as exc:
            fallback = select_rule_supervisor_route(
                message, history, residents, excluded_agents=set(attempted),
            )
            await self._record(task_id, "agent.fallback.activated", {
                "component": "supervisor_router",
                "reason": str(exc),
                "fallback": "rule_supervisor",
                "agent_id": fallback.agent_id,
            })
            return fallback

    async def _record(
        self,
        task_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type=event_type,
                node="butler_supervisor",
                data=data,
            )


def select_rule_supervisor_route(
    message: str,
    history: list[dict[str, Any]],
    residents: list[Resident],
    *,
    excluded_agents: set[str] | None = None,
) -> RouteDecision:
    """Conservative fallback/Oracle. Hybrid mode does not use this as primary routing."""
    excluded = excluded_agents or set()
    text = message.lower()
    recent = " ".join(str(item.get("content", "")) for item in history[-6:]).lower()
    resident = next((
        item for item in residents
        if item.name.lower() in f"{recent} {text}"
        or item.resident_id.lower() in f"{recent} {text}"
    ), None)
    rules: tuple[tuple[AgentId, tuple[str, ...]], ...] = (
        ("care_agent", ("护理员", "照护任务", "通知", "联系")),
        ("health_agent", ("健康", "心率", "血氧", "睡眠", "生命体征", "趋势", "老人", "长者")),
        ("security_agent", ("门锁", "门禁", "摄像头", "燃气", "烟雾", "报警", "安防")),
        ("energy_agent", ("能耗", "用电", "电表", "电费", "负载", "充电")),
        ("housekeeping_agent", ("扫地", "清洁", "拖地", "家务", "机器人")),
        ("device_ops_agent", ("离线", "故障", "设备状态", "td", "mcp", "网关", "接入")),
        ("environment_agent", ("灯", "空调", "温度", "湿度", "窗帘", "风扇", "空气", "房间")),
    )
    combined = f"{recent} {text}"
    for agent_id, terms in rules:
        if agent_id not in excluded and any(term in combined for term in terms):
            return RouteDecision(
                agent_id=agent_id,
                resolved_request=message,
                resident_id=resident.resident_id if resident else None,
                confidence=0.55,
                reason="rule fallback matched bounded domain vocabulary",
            )
    if resident is not None and "health_agent" not in excluded:
        return RouteDecision(
            agent_id="health_agent",
            resolved_request=message,
            resident_id=resident.resident_id,
            confidence=0.5,
            reason="rule fallback inherited an explicit resident reference",
        )
    if "environment_agent" not in excluded:
        return RouteDecision(
            agent_id="environment_agent",
            resolved_request=message,
            confidence=0.25,
            reason="rule fallback default",
        )
    return RouteDecision(
        action="clarify",
        resolved_request=message,
        confidence=0.0,
        reason="rule fallback exhausted available agents",
        clarification="我还无法确定应由哪个专业 Agent 处理，请补充目标对象和希望查询或执行的内容。",
    )


def select_supervisor_route(
    message: str,
    history: list[dict[str, Any]],
    residents: list[Resident],
) -> RouteDecision:
    """Backward-compatible rule Oracle used by tests and explicit rule mode."""
    return select_rule_supervisor_route(message, history, residents)


def _tool_arguments(result: Any) -> dict[str, Any]:
    tool_calls = getattr(result, "tool_calls", None) or []
    if tool_calls:
        arguments = tool_calls[0].get("args", {})
        return arguments if isinstance(arguments, dict) else json.loads(arguments)
    additional = getattr(result, "additional_kwargs", {}) or {}
    calls = additional.get("tool_calls", [])
    if calls:
        arguments = calls[0].get("function", {}).get("arguments", {})
        return arguments if isinstance(arguments, dict) else json.loads(arguments)
    raise ValueError("supervisor did not return a tool call")


__all__ = [
    "AGENT_REGISTRY",
    "ROUTE_DECISION_SCHEMA",
    "RouteDecision",
    "StructuredSupervisorRouter",
    "select_rule_supervisor_route",
    "select_supervisor_route",
]
