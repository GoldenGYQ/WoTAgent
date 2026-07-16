"""LangChain model adapter for the strongly typed LangGraph planner node."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import ValidationError, validate

from ..domain import TaskPlan
from ..prompting import (
    DEVICE_PLANNING_CONTRACT,
    AgentPromptProfile,
    compact_history,
    context_message,
    profile_for,
    prompt_system,
)
from ..telemetry import InMemoryTraceStore, extract_usage_with_cost
from ..wot import load_tds


_LOCATION_TERMS: dict[str, tuple[str, ...]] = {
    "living_room": ("客厅", "起居室", "living room", "parlor"),
    "bedroom1": ("主卧", "卧室1", "bedroom1", "master bedroom"),
    "bedroom2": ("次卧", "卧室2", "bedroom2", "guest bedroom"),
    "kitchen": ("厨房", "kitchen"),
    "study": ("书房", "study"),
    "bathroom1": ("主卫", "卫生间1", "bathroom1"),
    "bathroom2": ("客卫", "卫生间2", "bathroom2"),
}

_DEVICE_TERMS: dict[str, tuple[str, ...]] = {
    "light": ("灯", "照明", "light"),
    "ac": ("空调", "ac", "air conditioner"),
    "fan": ("风扇", "fan"),
    "exhaust": ("排风", "换气", "exhaust"),
    "humidifier": ("加湿器", "humidifier"),
    "purifier": ("净化器", "purifier"),
    "tv": ("电视", "tv"),
    "gas_sensor": ("燃气", "gas"),
    "health": ("手环", "床垫", "健康", "心率", "血氧", "wristband", "mattress"),
    "environment": ("环境传感器", "温湿度", "environment sensor"),
    "window_covering_controller": ("百叶窗", "窗帘", "blind", "blinds", "shade", "curtain"),
    "dimmable_light": ("调光灯", "dimmer", "dimmable light"),
    "laundry_dryer": ("烘干机", "dryer"),
    "laundry_washer": ("洗衣机", "washer"),
    "dishwasher": ("洗碗机", "dishwasher"),
    "refrigerator": ("冰箱", "refrigerator", "fridge"),
    "freezer": ("冷冻柜", "freezer"),
    "heat_pump": ("热泵", "heat pump"),
    "security": ("门锁", "门禁", "摄像头", "烟雾", "安防", "security"),
    "energy": ("电表", "能耗", "充电桩", "用电", "energy"),
    "housekeeping": ("扫地机器人", "清洁", "家务", "vacuum"),
    "device_ops": ("网关", "设备运维", "设备离线", "gateway"),
}


def capability_context(message: str = "", max_devices: int = 8) -> list[dict[str, Any]]:
    """Return a compact, model-safe capability view instead of complete TDs."""
    all_devices = [
        {
            "device_id": td.device_id,
            "title": td.title,
            "location": td.location,
            "location_aliases": list(_LOCATION_TERMS.get(td.location, ())),
            "actions": [
                {"name": action.name, "input_schema": action.input_schema}
                for action in td.actions
            ],
            "properties": [prop.name for prop in td.properties],
        }
        for td in load_tds()
    ]
    return select_capability_context(all_devices, message, max_devices=max_devices)


def select_capability_context(
    all_devices: list[dict[str, Any]],
    message: str = "",
    *,
    max_devices: int = 8,
) -> list[dict[str, Any]]:
    """Select relevant capabilities regardless of whether TDs came from disk or MCP."""
    all_devices = [
        {
            **device,
            "location_aliases": sorted(
                set(device.get("location_aliases", []))
                | set(_LOCATION_TERMS.get(str(device.get("location", "")), ()))
            ),
        }
        for device in all_devices
    ]
    text = message.lower().strip()
    if not text:
        return all_devices
    locations = {
        location for location, terms in _LOCATION_TERMS.items()
        if any(term in text for term in terms)
    }
    device_types = {
        device_type for device_type, terms in _DEVICE_TERMS.items()
        if any(term in text for term in terms)
    }
    for device in all_devices:
        location = str(device.get("location", ""))
        location_text = location.replace("_", " ").lower()
        if location_text and location_text in text:
            locations.add(location)
        device_type = str(device.get("device_type", "")).lower()
        type_text = device_type.replace("_", " ")
        distinctive = type_text.rsplit(" ", 1)[-1]
        if type_text and (type_text in text or distinctive in text):
            device_types.add(device_type)
    if not locations and not device_types:
        return all_devices

    def matches(device: dict[str, Any]) -> bool:
        title = device["title"].lower()
        location_match = not locations or device["location"] in locations
        actual_type = str(device.get("device_type", "")).lower()
        type_match = not device_types or any(
            device_type in title
            or device_type == actual_type
            or (device_type == "ac" and actual_type == "air_conditioner")
            for device_type in device_types
        )
        return location_match and type_match

    selected = [device for device in all_devices if matches(device)]
    selected.sort(
        key=lambda device: max(
            (len(alias) for alias in device.get("aliases", []) if alias.lower() in text),
            default=0,
        ),
        reverse=True,
    )
    return selected[:max_devices] if selected else all_devices


@dataclass
class LangChainTaskPlanner:
    """Convert one forced Function Calling result into a validated TaskPlan."""

    runnable: Any
    trace_store: InMemoryTraceStore | None = None
    capability_provider: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None
    thinking_runnable: Any | None = None
    agent_profile: AgentPromptProfile | None = None

    async def __call__(self, state: dict[str, Any]) -> TaskPlan:
        thinking = bool(state.get("thinking", False))
        if thinking and self.thinking_runnable is None:
            raise ValueError("thinking mode is not available on this server")
        message = state.get("message", "")
        provided = state.get("context", {}).get("devices")
        if provided:
            all_devices = list(provided)
        elif self.capability_provider is not None:
            all_devices = await self.capability_provider()
        else:
            all_devices = capability_context("")
        selection_text = _capability_selection_text(
            message,
            state.get("conversation_history", []),
        )
        devices = select_capability_context(all_devices, selection_text)
        runtime_context = state.get("context", {}).get("runtime", {})
        resumed = _resume_pending_plan(message, runtime_context)
        if resumed is not None:
            violations = _capability_violations(
                resumed,
                all_devices,
                str(runtime_context["pending_goal"].get("user_message", "")),
            )
            if violations:
                raise ValueError(
                    "pending goal no longer matches current TD capabilities: "
                    + "; ".join(violations)
                )
            if self.trace_store is not None and state.get("task_id"):
                await self.trace_store.record(
                    task_id=state["task_id"],
                    type="context.pending_goal.resumed",
                    node="planner",
                    data={
                        "source_task_id": runtime_context["pending_goal"].get("source_task_id"),
                        "current_role": runtime_context.get("current_role"),
                        "intent": resumed.intent,
                        "step_count": len(resumed.steps),
                    },
                )
            return resumed

        profile = self.agent_profile or profile_for(
            state.get("context", {}).get("delegated_agent")
        )
        system = prompt_system(profile, DEVICE_PLANNING_CONTRACT)
        runtime_message = context_message("runtime_context", runtime_context)
        capability_message = context_message("capability_context", {"devices": devices})
        history = compact_history(state.get("conversation_history", []))
        task_id = state.get("task_id", "")
        messages: list[Any] = [
            ("system", system),
            ("human", runtime_message),
            ("human", capability_message),
        ]
        for item in history:
            content = str(item.get("content", ""))
            if item.get("role") == "assistant":
                messages.append(("assistant", content))
            elif item.get("role") == "user":
                messages.append(("human", content))

        analysis = ""
        if thinking:
            analysis = await self._analyze(state, messages, devices)
        user_prompt = state.get("message", "")
        if analysis:
            user_prompt += (
                "\n\nPrivate planning analysis (advisory only; verify it against runtime facts "
                f"and TD capabilities):\n{analysis}"
            )
        messages.append(("human", user_prompt))

        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="planner",
                data={
                    "purpose": "task_planning",
                    "prompt_version": "planner-v3",
                    "device_count": len(devices),
                    "all_device_count": len(all_devices),
                    "message_length": len(state.get("message", "")),
                    "attempt": 1,
                    "thinking": thinking,
                    "model_thinking": False,
                    "analysis_available": bool(analysis),
                    "agent_id": profile.agent_id,
                    "context_pack_version": "context-pack-v1",
                    "system_characters": len(system),
                    "runtime_context_characters": len(runtime_message),
                    "capability_context_characters": len(capability_message),
                    "history_messages": len(history),
                    "history_characters": sum(len(item["content"]) for item in history),
                },
            )
        started = time.perf_counter()
        result = await self.runnable.ainvoke(messages)
        await self._record_model_completion(state, result, started, attempt=1)
        arguments = _extract_tool_arguments(result)
        plan = TaskPlan.model_validate(arguments)
        violations = _capability_violations(plan, devices, message)
        if violations:
            if self.trace_store is not None and task_id:
                await self.trace_store.record(
                    task_id=task_id,
                    type="plan.validation.failed",
                    node="planner",
                    data={"attempt": 1, "violations": violations},
                )
                await self.trace_store.record(
                    task_id=task_id,
                    type="model.request.started",
                    node="planner",
                    data={
                        "purpose": "task_planning_repair",
                        "prompt_version": "planner-v3",
                        "attempt": 2,
                        "thinking": thinking,
                        "model_thinking": False,
                    },
                )
            repair_prompt = (
                "The generated plan violates the current TD capability contract: "
                + "; ".join(violations)
                + ". Regenerate the complete plan using only the listed capabilities. "
                "Preserve the user's requested action direction; never replace turnOff with turnOn "
                "or turnOn with turnOff to make a plan valid. Clarify instead when needed."
            )
            # Start a fresh constrained attempt. Replaying an assistant tool call
            # without a matching ToolMessage violates the chat protocol used by
            # OpenAI-compatible providers.
            repair_messages = [
                *messages[:-1],
                ("human", f"{state.get('message', '')}\n\nPlanner correction: {repair_prompt}"),
            ]
            repair_started = time.perf_counter()
            result = await self.runnable.ainvoke(repair_messages)
            await self._record_model_completion(state, result, repair_started, attempt=2)
            arguments = _extract_tool_arguments(result)
            plan = TaskPlan.model_validate(arguments)
            violations = _capability_violations(plan, devices, message)
            if violations:
                raise ValueError(
                    "planner returned a TD-invalid plan after one repair: "
                    + "; ".join(violations)
                )
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.reasoning.summary",
                node="planner",
                data={
                    "summary": plan.summary,
                    "intent": plan.intent,
                    "step_count": len(plan.steps),
                },
            )
        return plan

    async def _analyze(
        self,
        state: dict[str, Any],
        planner_messages: list[Any],
        devices: list[dict[str, Any]],
    ) -> str:
        task_id = state.get("task_id", "")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="planner",
                data={
                    "purpose": "task_analysis",
                    "prompt_version": "planner-analysis-v2",
                    "device_count": len(devices),
                    "thinking": True,
                    "model_thinking": True,
                },
            )
        profile = self.agent_profile or profile_for(
            state.get("context", {}).get("delegated_agent")
        )
        analysis_messages = [
            (
                "system",
                prompt_system(profile, (
                    "Private analysis contract: analyze the user's "
                "intent, references, target scope, dependencies, and ambiguities using only the "
                "provided runtime facts and TD capabilities. Do not call tools and do not claim "
                "that any action was executed. Return concise planning guidance for a separate "
                    "structured planner."
                )),
            ),
            ("human", context_message(
                "runtime_context",
                state.get("context", {}).get("runtime", {}),
            )),
            ("human", context_message("capability_context", {"devices": devices})),
            *planner_messages[3:],
            ("human", state.get("message", "")),
        ]
        started = time.perf_counter()
        result = await self.thinking_runnable.ainvoke(analysis_messages)
        analysis = _extract_content(result).strip()
        if not analysis:
            raise ValueError("thinking planner returned empty analysis")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.completed",
                node="planner",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "purpose": "task_analysis",
                    "prompt_version": "planner-analysis-v2",
                    "model": _extract_model_name(result),
                    "thinking": True,
                    "model_thinking": True,
                    "analysis_characters": len(analysis),
                    **extract_usage_with_cost(result),
                },
            )
        return analysis

    async def _record_model_completion(
        self,
        state: dict[str, Any],
        result: Any,
        started: float,
        *,
        attempt: int,
    ) -> None:
        task_id = state.get("task_id", "")
        if self.trace_store is None or not task_id:
            return
        await self.trace_store.record(
            task_id=task_id,
            type="model.request.completed",
            node="planner",
            duration_ms=(time.perf_counter() - started) * 1000,
            data={
                "purpose": "task_planning" if attempt == 1 else "task_planning_repair",
                "prompt_version": "planner-v3",
                "attempt": attempt,
                "model": _extract_model_name(result),
                "thinking": bool(state.get("thinking", False)),
                "model_thinking": False,
                **extract_usage_with_cost(result),
            },
        )


def _extract_tool_arguments(result: Any) -> dict[str, Any]:
    tool_calls = getattr(result, "tool_calls", None) or []
    if tool_calls:
        call = tool_calls[0]
        if call.get("name") != "generate_plan":
            raise ValueError(f"unexpected planner tool: {call.get('name')}")
        arguments = call.get("args", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("planner tool arguments must be an object")
        return arguments

    raw_calls = getattr(result, "additional_kwargs", {}).get("tool_calls", [])
    if not raw_calls:
        raise ValueError("planner returned no generate_plan tool call")
    function = raw_calls[0].get("function", {})
    if function.get("name") != "generate_plan":
        raise ValueError(f"unexpected planner tool: {function.get('name')}")
    arguments = function.get("arguments", {})
    return json.loads(arguments) if isinstance(arguments, str) else arguments


def _extract_content(result: Any) -> str:
    message = result
    if isinstance(result, dict):
        messages = result.get("messages", [])
        if messages:
            message = messages[-1]
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")

def _extract_model_name(result: Any) -> str:
    metadata = getattr(result, "response_metadata", None) or {}
    return str(metadata.get("model_name") or metadata.get("model") or "unknown")


def _resume_pending_plan(message: str, runtime_context: dict[str, Any]) -> TaskPlan | None:
    pending = runtime_context.get("pending_goal")
    if not isinstance(pending, dict) or not _is_retry_request(message):
        return None
    raw_plan = pending.get("plan")
    if not isinstance(raw_plan, dict):
        return None
    payload = dict(raw_plan)
    payload.pop("plan_id", None)
    payload["summary"] = payload.get("summary") or pending.get("user_message", "继续上一任务")
    return TaskPlan.model_validate(payload)


def _is_retry_request(message: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?]", "", message.lower())
    exact = {"现在呢", "那现在呢", "继续", "执行吧", "继续执行", "接着做"}
    markers = ("再试", "重试", "试试看", "试试就知道", "现在可以", "重新执行")
    return normalized in exact or any(marker in normalized for marker in markers)


def _capability_violations(
    plan: TaskPlan,
    devices: list[dict[str, Any]],
    message: str = "",
) -> list[str]:
    by_id = {str(device.get("device_id")): device for device in devices}
    violations: list[str] = []
    for step in plan.steps:
        device = by_id.get(step.device_id)
        if device is None:
            violations.append(f"step {step.id}: device '{step.device_id}' is not registered")
            continue
        if step.kind != "action":
            continue
        action = next(
            (item for item in device.get("actions", []) if item.get("name") == step.action),
            None,
        )
        if action is None:
            violations.append(
                f"step {step.id}: action '{step.action}' is not declared by {step.device_id}"
            )
            continue
        try:
            validate(instance=step.input, schema=action.get("input_schema") or {})
        except ValidationError as exc:
            violations.append(f"step {step.id}: invalid '{step.action}' input: {exc.message}")
    normalized_message = message.lower()
    requested_off = any(
        marker in normalized_message
        for marker in ("关掉", "关闭", "关灯", "turn off", "switch off", "shut off")
    )
    requested_on = any(
        marker in normalized_message
        for marker in ("打开", "开启", "开灯", "turn on", "switch on")
    )
    actions = [step.action for step in plan.steps if step.kind == "action"]
    if requested_off and any(action == "turnOn" for action in actions):
        violations.append("plan reverses the requested power-off intent with turnOn")
    if requested_on and any(action == "turnOff" for action in actions):
        violations.append("plan reverses the requested power-on intent with turnOff")
    return violations


def _capability_selection_text(
    message: str,
    history: list[dict[str, str]],
) -> str:
    if _mentions_device_scope(message):
        return message
    for item in reversed(history):
        if item.get("role") == "user" and _mentions_device_scope(item.get("content", "")):
            return f"{message} {item['content']}"
    return message


def _mentions_device_scope(message: str) -> bool:
    text = message.lower()
    return any(
        term in text
        for terms in (*_LOCATION_TERMS.values(), *_DEVICE_TERMS.values())
        for term in terms
    )
