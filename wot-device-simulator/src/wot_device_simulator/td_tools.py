"""Compile WoT Thing Descriptions into executable MCP tool contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from .td import ThingDescription


class TDToolCompileError(ValueError):
    """Raised when a TD cannot be represented as an unambiguous MCP tool."""


@dataclass(frozen=True)
class TDToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    device_id: str
    operation: Literal["read", "invoke"]
    affordance: str
    title: str
    location: str
    properties: tuple[str, ...] = ()


def _identifier(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    if not normalized:
        raise TDToolCompileError(f"cannot derive an MCP identifier from {value!r}")
    return normalized


def action_tool_name(device_id: str, action: str) -> str:
    return f"{_identifier(device_id)}__action__{_identifier(action)}"


def state_tool_name(device_id: str) -> str:
    return f"{_identifier(device_id)}__read_state"


def _object_schema(schema: dict[str, Any], *, source: str) -> dict[str, Any]:
    if not schema:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    if schema.get("type") != "object":
        raise TDToolCompileError(
            f"{source} input must be an object schema so MCP arguments have stable names"
        )
    compiled = dict(schema)
    compiled.setdefault("properties", {})
    compiled.setdefault("additionalProperties", False)
    return compiled


def compile_td_tools(td: ThingDescription) -> list[TDToolDefinition]:
    """Compile readable state and every TD action into MCP tool definitions."""
    if not td.device_id:
        raise TDToolCompileError("TD id must contain a device identifier")

    definitions: list[TDToolDefinition] = []
    readable = [prop.name for prop in td.properties if prop.readable]
    if readable:
        definitions.append(TDToolDefinition(
            name=state_tool_name(td.device_id),
            description=(
                f"Read {td.title or td.device_id} state in {td.location or 'unknown location'}. "
                f"Readable properties: {', '.join(readable)}."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            device_id=td.device_id,
            operation="read",
            affordance="state",
            title=td.title,
            location=td.location,
            properties=tuple(readable),
        ))

    for action in td.actions:
        definitions.append(TDToolDefinition(
            name=action_tool_name(td.device_id, action.name),
            description=action.description or (
                f"Invoke {action.name} on {td.title or td.device_id} "
                f"in {td.location or 'unknown location'}."
            ),
            input_schema=_object_schema(
                action.input_schema,
                source=f"{td.device_id}.{action.name}",
            ),
            device_id=td.device_id,
            operation="invoke",
            affordance=action.name,
            title=td.title,
            location=td.location,
        ))
    return definitions


def compile_tool_catalog(tds: list[ThingDescription]) -> list[TDToolDefinition]:
    catalog: list[TDToolDefinition] = []
    names: set[str] = set()
    for td in tds:
        for definition in compile_td_tools(td):
            if definition.name in names:
                raise TDToolCompileError(f"duplicate generated MCP tool name: {definition.name}")
            names.add(definition.name)
            catalog.append(definition)
    return catalog


__all__ = [
    "TDToolCompileError",
    "TDToolDefinition",
    "action_tool_name",
    "compile_td_tools",
    "compile_tool_catalog",
    "state_tool_name",
]
