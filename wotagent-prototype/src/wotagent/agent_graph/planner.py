"""LangChain model adapter for the strongly typed LangGraph planner node."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import ValidationError, validate

from ..domain import TaskPlan
from ..wot import load_tds
from ..telemetry import InMemoryTraceStore, extract_usage_with_cost


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
            "location_aliases": list(_LOCATION_TERMS.get(str(device.get("location", "")), ())),
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
    if not locations and not device_types:
        return all_devices

    def matches(device: dict[str, Any]) -> bool:
        title = device["title"].lower()
        location_match = not locations or device["location"] in locations
        type_match = not device_types or any(device_type in title for device_type in device_types)
        return location_match and type_match

    selected = [device for device in all_devices if matches(device)]
    return selected[:max_devices] if selected else all_devices


@dataclass
class LangChainTaskPlanner:
    """Convert one forced Function Calling result into a validated TaskPlan."""

    runnable: Any
    trace_store: InMemoryTraceStore | None = None
    capability_provider: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None

    async def __call__(self, state: dict[str, Any]) -> TaskPlan:
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

        system = (
            "You are the WoTAgent planner. Return only the generate_plan tool call. "
            "Use only device_id, action names, and exact input_schema parameter names listed below. "
            "Location aliases identify the same canonical room. "
            "Runtime facts are authoritative and must not be inferred from assistant prose. "
            "Never ask the user whether their role changed; Policy checks current_role after planning. "
            "For elliptical follow-ups, prefer the pending goal or last actionable task when present. "
            "Use intent=clarify only when no safe target can be resolved. Do not decide permissions.\n"
            f"Runtime facts: {json.dumps(runtime_context, ensure_ascii=False)}\n"
            f"Available devices: {json.dumps(devices, ensure_ascii=False)}"
        )
        task_id = state.get("task_id", "")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="planner",
                data={
                    "purpose": "task_planning",
                    "prompt_version": "planner-v2.1",
                    "device_count": len(devices),
                    "all_device_count": len(all_devices),
                    "message_length": len(state.get("message", "")),
                    "attempt": 1,
                },
            )
        started = time.perf_counter()
        messages: list[Any] = [("system", system)]
        for item in state.get("conversation_history", []):
            content = str(item.get("content", ""))
            if item.get("role") == "assistant":
                messages.append(("assistant", content))
            elif item.get("role") == "user":
                messages.append(("human", content))
        messages.append(("human", state.get("message", "")))
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
                        "prompt_version": "planner-v2.1",
                        "attempt": 2,
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
                "prompt_version": "planner-v2.1",
                "attempt": attempt,
                "model": _extract_model_name(result),
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
