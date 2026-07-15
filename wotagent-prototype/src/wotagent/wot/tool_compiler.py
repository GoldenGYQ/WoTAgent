"""Compile normalized Thing Descriptions into MCP tool definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from .td import ThingDescription


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
        raise ValueError(f"cannot derive an MCP identifier from {value!r}")
    return normalized


def compile_td_tools(td: ThingDescription) -> list[TDToolDefinition]:
    if not td.device_id:
        raise ValueError("TD id must contain a device identifier")
    definitions: list[TDToolDefinition] = []
    readable = tuple(prop.name for prop in td.properties if prop.readable)
    if readable:
        definitions.append(TDToolDefinition(
            name=f"{_identifier(td.device_id)}__read_state",
            description=f"Read {td.title or td.device_id} state.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            device_id=td.device_id,
            operation="read",
            affordance="state",
            title=td.title,
            location=td.location,
            properties=readable,
        ))
    for action in td.actions:
        schema = dict(action.input_schema)
        if schema.get("type") != "object":
            raise ValueError(f"{td.device_id}.{action.name} input must be an object schema")
        schema.setdefault("properties", {})
        schema.setdefault("additionalProperties", False)
        definitions.append(TDToolDefinition(
            name=f"{_identifier(td.device_id)}__action__{_identifier(action.name)}",
            description=action.description or f"Invoke {action.name} on {td.title or td.device_id}.",
            input_schema=schema,
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
                raise ValueError(f"duplicate generated MCP tool name: {definition.name}")
            names.add(definition.name)
            catalog.append(definition)
    return catalog


__all__ = ["TDToolDefinition", "compile_td_tools", "compile_tool_catalog"]
