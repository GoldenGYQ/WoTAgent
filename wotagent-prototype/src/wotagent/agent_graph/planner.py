"""LangChain model adapter for the strongly typed LangGraph planner node."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

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
        devices = select_capability_context(all_devices, message)
        system = (
            "You are the WoTAgent planner. Return only the generate_plan tool call. "
            "Use only device_id, action names, and exact input_schema parameter names listed below. "
            "Location aliases identify the same canonical room. "
            "Use intent=clarify when the target is ambiguous. Do not decide permissions.\n"
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
                    "prompt_version": "planner-v2.0",
                    "device_count": len(devices),
                    "all_device_count": len(all_devices),
                    "message_length": len(state.get("message", "")),
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
        arguments = _extract_tool_arguments(result)
        plan = TaskPlan.model_validate(arguments)
        if self.trace_store is not None and task_id:
            usage = extract_usage_with_cost(result)
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.completed",
                node="planner",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "purpose": "task_planning",
                    "prompt_version": "planner-v2.0",
                    "model": _extract_model_name(result),
                    **usage,
                },
            )
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
