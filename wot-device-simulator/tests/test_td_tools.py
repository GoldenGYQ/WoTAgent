"""Contract tests for the TD -> MCP tool compiler and registration path."""

from __future__ import annotations

import pytest

from wot_device_simulator.mcp_server import _build_server
from wot_device_simulator.td import TDAction, ThingDescription, get_device_by_id, load_tds
from wot_device_simulator.td_tools import (
    TDToolCompileError,
    action_tool_name,
    compile_td_tools,
    compile_tool_catalog,
    state_tool_name,
)


def test_compiler_uses_td_action_input_as_mcp_schema() -> None:
    td = get_device_by_id("light-001")
    assert td is not None

    tools = {tool.name: tool for tool in compile_td_tools(td)}
    brightness = tools[action_tool_name("light-001", "setBrightness")]

    assert brightness.input_schema == next(
        action.input_schema for action in td.actions if action.name == "setBrightness"
    )
    assert brightness.input_schema["required"] == ["brightness"]
    assert brightness.input_schema["properties"]["brightness"]["maximum"] == 100
    assert state_tool_name("light-001") in tools


def test_compiler_rejects_ambiguous_scalar_action_input() -> None:
    td = ThingDescription(
        id="urn:test:bad-001",
        title="BadDevice",
        actions=[TDAction(name="setValue", input_type="number", input_schema={"type": "number"})],
    )

    with pytest.raises(TDToolCompileError, match="object schema"):
        compile_td_tools(td)


def test_all_repository_tds_compile_without_name_collisions() -> None:
    catalog = compile_tool_catalog(load_tds())
    assert catalog
    assert len({tool.name for tool in catalog}) == len(catalog)


@pytest.mark.asyncio
async def test_fastmcp_registers_generated_schema_and_executes_it() -> None:
    mcp = _build_server()
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    tool_name = action_tool_name("light-001", "setBrightness")

    assert tool_name in tools
    assert tools[tool_name].parameters["required"] == ["brightness"]

    result = await mcp.call_tool(tool_name, {"brightness": 37})
    assert result.structured_content["success"] is True
    assert result.structured_content["new_state"]["brightness"] == 37
