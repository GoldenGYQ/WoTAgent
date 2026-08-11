"""Natural-language eldercare command planning and execution."""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from fastmcp import Client
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ...prompting import (
    ELDERCARE_PLANNING_CONTRACT,
    compact_history,
    context_message,
    profile_for,
    prompt_system,
)
from ...telemetry import InMemoryTraceStore, extract_usage_with_cost
from .mcp_server import build_eldercare_mcp
from .models import DelegatedAction, DelegatedTask, Delegation, Resident
from .runtime import EldercareRuntime


class EldercareCommandAction(BaseModel):
    name: Literal[
        "read_health_summary",
        "adjust_temperature",
        "notify_caregiver",
        "create_care_task",
    ]
    target_temperature: float | None = Field(default=None, ge=16, le=30)
    message: str = ""
    urgency: Literal["medium", "high", "critical"] = "medium"
    title: str = ""
    priority: Literal["medium", "high", "critical"] = "medium"


class EldercareCommandPlan(BaseModel):
    intent: Literal["query", "control", "mixed", "clarify"]
    resident_id: str | None = None
    summary: str = ""
    clarification: str = ""
    actions: list[EldercareCommandAction] = Field(default_factory=list)


ELDERCARE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_eldercare_plan",
        "description": "Create a constrained eldercare query or action plan",
        "parameters": EldercareCommandPlan.model_json_schema(),
    },
}


def is_eldercare_request(
    message: str,
    history: list[dict[str, Any]],
    residents: list[Resident],
) -> bool:
    text = message.lower()
    identity_terms = [
        term.lower()
        for resident in residents
        for term in (resident.name, resident.resident_id)
    ]
    domain_terms = (
        "老人", "长者", "护理员", "照护", "养老", "心率", "血氧", "睡眠",
        "跌倒", "生命体征", "健康状态", "健康摘要", "在床",
    )
    if any(term in text for term in identity_terms) or any(term in text for term in domain_terms):
        return True

    # Elliptical follow-ups inherit eldercare context unless the new message
    # explicitly switches back to an ordinary smart-home target.
    if any(term in text for term in ("灯", "客厅", "厨房", "电视", "窗帘")):
        return False
    recent = " ".join(str(item.get("content", "")) for item in history[-4:]).lower()
    has_context = any(term in recent for term in identity_terms) or any(
        term in recent for term in domain_terms
    )
    return has_context and any(
        term in text for term in ("现在", "怎么样", "通知", "安排", "调整", "温度", "房间", "查一下")
    )


def resolve_resident(
    message: str,
    history: list[dict[str, Any]],
    residents: list[Resident],
) -> Resident | None:
    for text in [message, *reversed([str(item.get("content", "")) for item in history])]:
        lowered = text.lower()
        for resident in residents:
            if (
                resident.name.lower() in lowered
                or resident.resident_id.lower() in lowered
                or _room_matches(lowered, resident.room)
            ):
                return resident
    return residents[0] if len(residents) == 1 else None


def _room_matches(text: str, room: str) -> bool:
    normalized_text = _normalize_room_token(text)
    normalized_room = _normalize_room_token(room)
    if not normalized_room:
        return False
    return normalized_room in normalized_text


def _normalize_room_token(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


class EldercareCommandPlanner:
    def __init__(
        self,
        runnable: Any,
        *,
        trace_store: InMemoryTraceStore | None = None,
        thinking_runnable: Any | None = None,
    ) -> None:
        self.runnable = runnable
        self.trace_store = trace_store
        self.thinking_runnable = thinking_runnable

    async def plan(
        self,
        *,
        task_id: str,
        message: str,
        history: list[dict[str, Any]],
        residents: list[Resident],
        resolved_resident: Resident,
        role: str = "operator",
        thinking: bool = False,
    ) -> EldercareCommandPlan:
        system = prompt_system(
            profile_for("eldercare_butler"),
            ELDERCARE_PLANNING_CONTRACT,
        )
        messages: list[Any] = [
            SystemMessage(content=system),
            HumanMessage(content=context_message("resident_context", {
                "resolved_resident": resolved_resident.model_dump(mode="json"),
                "current_role": role,
                "allowed_actions": [
                    "read_health_summary",
                    "adjust_temperature",
                    "notify_caregiver",
                    "create_care_task",
                ],
            })),
        ]
        for item in compact_history(history, max_messages=6):
            message_type = HumanMessage if item["role"] == "user" else AIMessage
            messages.append(message_type(content=(
                f"Conversation reference ({item['role']}): {item['content']}"
            )))
        messages.append(HumanMessage(content=message))
        if thinking:
            if self.thinking_runnable is None:
                raise ValueError("thinking mode is not available for eldercare planning")
            if self.trace_store is not None:
                await self.trace_store.record(
                    task_id=task_id,
                    type="model.request.started",
                    node="eldercare_reasoner",
                    data={"purpose": "eldercare_private_analysis", "thinking": True},
                )
            analysis_started = time.perf_counter()
            analysis_result = await self.thinking_runnable.ainvoke([
                SystemMessage(content=prompt_system(
                    profile_for("eldercare_butler"),
                    "私有分析契约：分析居民、查询目标、动作、风险和必要参数。"
                    "只做规划分析，不调用工具，不声称已经执行。",
                )),
                HumanMessage(content=context_message("resident_context", {
                    "resolved_resident": resolved_resident.model_dump(mode="json"),
                    "available_actions": [
                        "read_health_summary",
                        "adjust_temperature",
                        "notify_caregiver",
                        "create_care_task",
                    ],
                })),
                *[
                    (
                        HumanMessage(content=f"Conversation reference (user): {item['content']}")
                        if item["role"] == "user"
                        else AIMessage(content=f"Conversation reference (assistant): {item['content']}")
                    )
                    for item in compact_history(history, max_messages=4)
                ],
                HumanMessage(content=message),
            ])
            if self.trace_store is not None:
                await self.trace_store.record(
                    task_id=task_id,
                    type="model.request.completed",
                    node="eldercare_reasoner",
                    duration_ms=(time.perf_counter() - analysis_started) * 1000,
                    data={
                        "purpose": "eldercare_private_analysis",
                        "thinking": True,
                        **extract_usage_with_cost(analysis_result),
                    },
                )
            analysis = str(getattr(analysis_result, "content", "") or "").strip()
            if analysis:
                messages.append(HumanMessage(content=(
                    "私有规划分析，仅作参考；仍须遵守居民目录和动作约束：\n" + analysis
                )))
        if self.trace_store is not None:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="eldercare_planner",
                data={
                    "purpose": "eldercare_command_planning",
                    "prompt_version": "eldercare-planner-v2",
                    "resident_id": resolved_resident.resident_id,
                    "agent_id": "eldercare_butler",
                    "context_pack_version": "context-pack-v1",
                },
            )
        started = time.perf_counter()
        result = await self.runnable.ainvoke(messages)
        if self.trace_store is not None:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.completed",
                node="eldercare_planner",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "purpose": "eldercare_command_planning",
                    "prompt_version": "eldercare-planner-v2",
                    **extract_usage_with_cost(result),
                },
            )
        plan = EldercareCommandPlan.model_validate(_tool_arguments(result))
        plan.resident_id = resolved_resident.resident_id
        return plan


class EldercareChatAgent:
    def __init__(
        self,
        runtime: EldercareRuntime,
        planner: EldercareCommandPlanner,
        *,
        trace_store: InMemoryTraceStore,
    ) -> None:
        self.runtime = runtime
        self.planner = planner
        self.trace_store = trace_store

    async def run(
        self,
        *,
        task_id: str,
        message: str,
        history: list[dict[str, Any]],
        role: str,
        thinking: bool = False,
        routed_agent: str | None = None,
        resident_id: str | None = None,
    ) -> dict[str, Any]:
        residents = self.runtime.store.list_residents()
        resident = next(
            (item for item in residents if item.resident_id == resident_id),
            None,
        ) if resident_id else resolve_resident(message, history, residents)
        await self.trace_store.record(
            task_id=task_id,
            type="agent.route.selected",
            node="domain_router",
            data={
                "route": "eldercare",
                "resident_id": resident.resident_id if resident else None,
            },
        )
        if resident is None:
            names = "、".join(item.name for item in residents)
            return {
                "status": "clarify",
                "response": f"请说明要操作哪位老人。目前可选：{names}。",
                "plan": None,
            }

        if routed_agent == "health_agent":
            finding = await self.runtime.health_agent.run(
                task_id=task_id,
                resident_id=resident.resident_id,
                delegation=Delegation(
                    agent="health_agent",
                    objective=message,
                    reason="Butler delegated a user health investigation",
                    expected_evidence=["与问题相关的健康事实", "数据时间或趋势证据"],
                ),
            )
            await self.trace_store.record(
                task_id=task_id,
                type="agent.finding.completed",
                node="health_agent",
                data={"finding": finding.model_dump(mode="json")},
            )
            return {
                "status": (
                    "succeeded"
                    if finding.status == "completed"
                    else "partial_success"
                    if finding.evidence
                    else finding.status
                ),
                "response": _health_finding_response(resident, finding),
                "plan": {
                    "intent": "query",
                    "summary": message,
                    "routed_agent": "health_agent",
                    "steps": [],
                },
                "finding": finding.model_dump(mode="json"),
            }

        plan = await self.planner.plan(
            task_id=task_id,
            message=message,
            history=history,
            residents=residents,
            resolved_resident=resident,
            role=role,
            thinking=thinking,
        )
        if plan.intent == "clarify":
            return {
                "status": "clarify",
                "response": plan.clarification or "请补充需要查询或处置的具体内容。",
                "plan": plan.model_dump(mode="json"),
            }

        responses: list[str] = []
        if any(action.name == "read_health_summary" for action in plan.actions):
            summary = await self._read_summary(task_id, resident)
            responses.append(_summary_response(summary))

        delegations = _delegations(plan, resident, message)
        graph_result: dict[str, Any] | None = None
        if delegations:
            graph_result = await self.runtime.execute_user_delegations(
                task_id=task_id,
                resident_id=resident.resident_id,
                message=message,
                delegations=[item.model_dump(mode="json") for item in delegations],
                role=role,
            )
            responses.append(_action_response(graph_result))

        if not responses:
            return {
                "status": "clarify",
                "response": "没有识别到可执行的养老查询或处置动作，请换一种说法。",
                "plan": plan.model_dump(mode="json"),
            }
        status = str(graph_result.get("status", "succeeded")) if graph_result else "succeeded"
        return {
            "status": status,
            "response": " ".join(responses),
            "plan": plan.model_dump(mode="json"),
            "child_results": graph_result.get("child_results", []) if graph_result else [],
        }

    async def _read_summary(self, task_id: str, resident: Resident) -> dict[str, Any]:
        uri = f"elderly://{resident.resident_id}/health/summary"
        async with Client(build_eldercare_mcp(self.runtime.store)) as client:
            contents = await client.read_resource(uri)
        payload = json.loads(contents[0].text)
        await self.trace_store.record(
            task_id=task_id,
            type="agent.resource.completed",
            node="health_agent",
            data={"mcp_server": "eldercare-health", "uri": uri, "output": payload},
        )
        return payload


def _delegations(
    plan: EldercareCommandPlan,
    resident: Resident,
    original_message: str,
) -> list[DelegatedTask]:
    environment: list[DelegatedAction] = []
    care: list[DelegatedAction] = []
    for action in plan.actions:
        if action.name == "adjust_temperature":
            if action.target_temperature is None:
                continue
            environment.append(DelegatedAction(name="adjust_temperature", arguments={
                "resident_id": resident.resident_id,
                "target_temperature": action.target_temperature,
                "reason": action.message or original_message,
            }))
        elif action.name == "notify_caregiver":
            care.append(DelegatedAction(name="notify_caregiver", arguments={
                "resident_id": resident.resident_id,
                "message": action.message or original_message,
                "urgency": action.urgency,
            }))
        elif action.name == "create_care_task":
            care.append(DelegatedAction(name="create_care_task", arguments={
                "resident_id": resident.resident_id,
                "title": action.title or original_message,
                "priority": action.priority,
            }))
    tasks: list[DelegatedTask] = []
    if environment:
        tasks.append(DelegatedTask(
            agent_id="environment_agent",
            objective=plan.summary or original_message,
            resident_scope=[resident.resident_id],
            allowed_mcp="eldercare-environment",
            actions=environment,
        ))
    if care:
        tasks.append(DelegatedTask(
            agent_id="care_agent",
            objective=plan.summary or original_message,
            resident_scope=[resident.resident_id],
            allowed_mcp="eldercare-care",
            actions=care,
        ))
    return tasks


def _summary_response(summary: dict[str, Any]) -> str:
    resident = summary["resident"]
    latest = summary.get("latest", {})
    labels = {
        "heart_rate": "心率",
        "spo2": "血氧",
        "sleep_quality": "睡眠",
        "room_temperature": "室温",
        "room_humidity": "湿度",
        "bed_occupancy": "在床",
    }
    values = []
    for key, label in labels.items():
        item = latest.get(key)
        if item:
            value = item["value"]
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            values.append(f"{label}{value}{item.get('unit', '')}")
    facts = "，".join(values) if values else "暂时没有最新传感器数据"
    risks = summary.get("open_risks", [])
    risk_text = f"当前有 {len(risks)} 个开放风险" if risks else "当前没有开放风险"
    return f"{resident['name']}当前状态：{facts}；{risk_text}。"


def _health_finding_response(resident: Resident, finding: Any) -> str:
    evidence_count = len(finding.evidence)
    if finding.status == "completed":
        return f"{resident.name}：{finding.finding}"
    if evidence_count:
        unresolved = "；".join(finding.unresolved_questions)
        suffix = f" 尚待确认：{unresolved}。" if unresolved else ""
        return f"{resident.name}：{finding.finding}{suffix}"
    return f"暂时无法完成对{resident.name}的健康调查：{finding.finding}"


def _action_response(result: dict[str, Any]) -> str:
    if result.get("status") == "denied":
        denied = result.get("denied_delegations", [])
        reason = denied[0].get("reason", "权限或委派范围不允许") if denied else "权限不足"
        return f"养老处置请求被拒绝：{reason}。"
    actions = [
        action
        for child in result.get("child_results", [])
        for action in child.get("actions", [])
    ]
    succeeded = [item for item in actions if item.get("output", {}).get("success")]
    modes = {
        str(item.get("output", {}).get("execution_mode"))
        for item in succeeded
        if item.get("output", {}).get("execution_mode")
    }
    mode_text = "，其中环境控制已通过 WoT/MCP 验证" if "wot_mcp" in modes else ""
    if len(succeeded) == len(actions) and actions:
        return f"已完成 {len(actions)} 个养老处置动作{mode_text}。"
    return f"养老处置部分完成：成功 {len(succeeded)}/{len(actions)} 个动作。"


def _tool_arguments(result: Any) -> dict[str, Any]:
    tool_calls = getattr(result, "tool_calls", None) or []
    if tool_calls:
        arguments = tool_calls[0].get("args", {})
        return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
    additional = getattr(result, "additional_kwargs", None) or {}
    call = additional.get("tool_calls", [{}])[0]
    arguments = call.get("function", {}).get("arguments", "{}")
    return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
