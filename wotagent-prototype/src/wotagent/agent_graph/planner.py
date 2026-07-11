"""LangChain model adapter for the strongly typed LangGraph planner node."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..domain import TaskPlan
from ..wot import load_tds


def capability_context() -> list[dict[str, Any]]:
    """Return a compact, model-safe capability view instead of complete TDs."""
    return [
        {
            "device_id": td.device_id,
            "title": td.title,
            "location": td.location,
            "actions": [action.name for action in td.actions],
            "properties": [prop.name for prop in td.properties],
        }
        for td in load_tds()
    ]


@dataclass
class LangChainTaskPlanner:
    """Convert one forced Function Calling result into a validated TaskPlan."""

    runnable: Any

    async def __call__(self, state: dict[str, Any]) -> TaskPlan:
        devices = state.get("context", {}).get("devices") or capability_context()
        system = (
            "You are the WoTAgent planner. Return only the generate_plan tool call. "
            "Use only device_id, actions, and properties listed below. "
            "Use intent=clarify when the target is ambiguous. Do not decide permissions.\n"
            f"Available devices: {json.dumps(devices, ensure_ascii=False)}"
        )
        result = await self.runnable.ainvoke([
            ("system", system),
            ("human", state.get("message", "")),
        ])
        arguments = _extract_tool_arguments(result)
        return TaskPlan.model_validate(arguments)


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
