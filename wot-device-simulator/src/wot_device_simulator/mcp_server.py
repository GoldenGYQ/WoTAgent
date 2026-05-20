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

from typing import Any

from .simulator import DeviceStateStore, get_simulator
from .td import find_devices, get_device_by_id, load_tds


def _build_server():
    from fastmcp import FastMCP

    sim = get_simulator(auto_start=True)
    mcp = FastMCP("wot-device-simulator")

    @mcp.tool()
    def list_devices(location: str | None = None) -> list[dict[str, Any]]:
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
        td = get_device_by_id(device_id)
        if td is None:
            return {"success": False, "error": f"TD '{device_id}' not found"}
        return {"success": True, "td": td.raw}

    @mcp.tool()
    def download_all_td() -> list[dict[str, Any]]:
        return [td.raw for td in load_tds()]

    @mcp.tool()
    def query_state(device_id: str) -> dict[str, Any]:
        DeviceStateStore.initialize()
        return {"device_id": device_id, "state": DeviceStateStore.get(device_id)}

    @mcp.tool()
    async def control_device(device_id: str, action: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return await sim.control_device(device_id=device_id, action=action, parameters=parameters or {})

    @mcp.tool()
    async def poll_once() -> dict[str, Any]:
        rules = await sim.poll_once()
        return {"triggered": len(rules), "rules": rules}

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

    return mcp


def main() -> None:
    mcp = _build_server()
    mcp.run()
