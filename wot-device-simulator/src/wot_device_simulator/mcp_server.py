"""MCP server for the WoT Device Simulator.

Run as a module (preferred):
    python -m wot_device_simulator.mcp_server

Or directly (also works):
    python src/wot_device_simulator/mcp_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as ``python path/to/mcp_server.py`` — FastMCP Client spawns
# the script this way in stdio mode, so relative imports won't work without
# adding the package parent to sys.path.
if __name__ == "__main__" and __package__ is None:
    _parent = Path(__file__).resolve().parent.parent
    if str(_parent) not in sys.path:
        sys.path.insert(0, str(_parent))
    __package__ = "wot_device_simulator"

import logging
from typing import Any

from .simulator import DeviceStateStore, get_simulator
from .td import find_devices, get_device_by_id, load_tds
from .td_tools import TDToolDefinition, compile_tool_catalog

logger = logging.getLogger(__name__)


def _register_td_tools(mcp, sim, definitions: list[TDToolDefinition]) -> None:
    """Register executable MCP tools compiled from TD affordances."""
    from fastmcp.tools.function_tool import FunctionTool

    for definition in definitions:
        if definition.operation == "read":
            async def handler(
                _device_id: str = definition.device_id,
            ) -> dict[str, Any]:
                DeviceStateStore.initialize()
                return {
                    "success": True,
                    "device_id": _device_id,
                    "state": DeviceStateStore.get(_device_id),
                }
        else:
            async def handler(
                _device_id: str = definition.device_id,
                _action: str = definition.affordance,
                **arguments: Any,
            ) -> dict[str, Any]:
                return await sim.control_device(
                    device_id=_device_id,
                    action=_action,
                    parameters=arguments,
                )

        tool = FunctionTool(
            name=definition.name,
            description=definition.description,
            parameters=definition.input_schema,
            fn=handler,
            return_type=dict[str, Any],
            run_in_thread=False,
            meta={
                "wot": {
                    "device_id": definition.device_id,
                    "operation": definition.operation,
                    "affordance": definition.affordance,
                    "title": definition.title,
                    "location": definition.location,
                    "properties": list(definition.properties),
                    "capabilities": list(definition.capabilities),
                    "mcp_scopes": list(definition.mcp_scopes),
                }
            },
        )
        mcp.add_tool(tool)


def _build_server(sim=None):
    """Build the FastMCP server with all IoT simulator tools.

    Args:
        sim: Optional SimulatorEngine instance. If omitted, creates one
             via the global singleton (``get_simulator(auto_start=True)``).
    """
    from fastmcp import FastMCP

    if sim is None:
        sim = get_simulator(auto_start=True)
    mcp = FastMCP("wot-device-simulator")

    @mcp.tool()
    def list_devices(location: str | None = None) -> list[dict[str, Any]]:
        logger.info("Sim MCP: list_devices(location=%s)", location)
        devices = find_devices(location=location) if location else find_devices()
        return [
            {
                "id": d.device_id,
                "title": d.title,
                "location": d.location,
                "capabilities": d.capabilities,
                "actions": [a.name for a in d.actions],
                "properties": [p.name for p in d.properties],
            }
            for d in devices
        ]

    @mcp.tool()
    def get_td(device_id: str) -> dict[str, Any]:
        logger.info("Sim MCP: get_td(device_id=%s)", device_id)
        td = get_device_by_id(device_id)
        if td is None:
            return {"success": False, "error": f"TD '{device_id}' not found"}
        return {"success": True, "td": td.raw}

    @mcp.tool()
    def download_all_td() -> list[dict[str, Any]]:
        logger.info("Sim MCP: download_all_td()")
        return [td.raw for td in load_tds()]

    @mcp.tool()
    def query_state(device_id: str) -> dict[str, Any]:
        logger.info("Sim MCP: query_state(device_id=%s)", device_id)
        DeviceStateStore.initialize()
        return {"device_id": device_id, "state": DeviceStateStore.get(device_id)}

    @mcp.tool()
    def query_all_states() -> dict[str, dict[str, Any]]:
        """Return current runtime state for ALL devices in one call."""
        logger.info("Sim MCP: query_all_states()")
        DeviceStateStore.initialize()
        return DeviceStateStore.get_all()

    @mcp.tool()
    def drift() -> dict[str, Any]:
        """Trigger one drift cycle — simulates environmental changes."""
        logger.info("Sim MCP: drift()")
        DeviceStateStore.drift()
        return {"success": True, "message": "Drift cycle completed"}

    @mcp.tool()
    async def control_device(device_id: str, action: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        logger.info("Sim MCP: control_device(device_id=%s, action=%s, params=%s)", device_id, action, parameters)
        return await sim.control_device(device_id=device_id, action=action, parameters=parameters or {})

    @mcp.tool()
    async def poll_once() -> dict[str, Any]:
        await sim.poll_once()
        return {"success": True, "message": "Drift cycle completed"}

    @mcp.tool()
    async def patch_state(device_id: str, property_name: str, value: Any) -> dict[str, Any]:
        return await sim.patch_state(device_id=device_id, property_name=property_name, value=value)

    @mcp.tool()
    async def set_environment(
        temperature: float | None = None,
        humidity: float | None = None,
        pm25: float | None = None,
        gas_level: float | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if temperature is not None:
            values["temperature"] = temperature
        if humidity is not None:
            values["humidity"] = humidity
        if pm25 is not None:
            values["pm25"] = pm25
        if gas_level is not None:
            values["gasLevel"] = gas_level
        return await sim.apply_environment(values=values, location=location)

    @mcp.tool()
    async def inject_event(event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return await sim.inject_event(event_type=event_type, data=data or {})

    _register_td_tools(mcp, sim, compile_tool_catalog(load_tds()))
    return mcp


def main() -> None:
    mcp = _build_server()
    mcp.run()


if __name__ == "__main__":
    main()
