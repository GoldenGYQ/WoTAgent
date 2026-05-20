"""IoT device control tools — LangChain tools wrapping WoT TD discovery.

Each tool corresponds to a device capability exposed via Thing Descriptions.
Tools prefer calling the simulation project via MCP first, falling back to
local DeviceStateStore when the MCP server is unavailable.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from ..auth import get_access_control
from ..events import Event, get_bus
from ..wot.td import find_devices, ThingDescription

# ---------------------------------------------------------------------------
# Tool implementations — sync (local-only fallback)
# ---------------------------------------------------------------------------


def _list_devices(location: str | None = None) -> list[dict[str, Any]]:
    """List all available IoT devices, optionally filtered by location.

    Args:
        location: Filter by room (parlor, bedroom, study). Optional.
    """
    devices = find_devices(location=location) if location else find_devices()
    from ..perception import DeviceStateStore
    return [
        {
            "id": d.device_id,
            "title": d.title,
            "location": d.location,
            "capabilities": d.capabilities,
            "actions": [a.name for a in d.actions],
            "properties": [p.name for p in d.properties],
            "state": DeviceStateStore.get(d.device_id),
        }
        for d in devices
    ]


def _control_device(
    device_id: str,
    action: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute an action on a specific IoT device — local implementation.

    Args:
        device_id: The device identifier (e.g. "light-001").
        action: The action name (e.g. "turnOn", "setBrightness").
        parameters: Action parameters as key-value pairs.
    """
    # Find device via TD
    from ..wot.td import get_device_by_id

    device = get_device_by_id(device_id)
    if device is None:
        candidates = find_devices()
        for d in candidates:
            if device_id in d.device_id or device_id in d.title:
                device = d
                break
    if device is None and not device_id:
        action_lower = action.lower()
        if any(
            token in action_lower
            for token in ("turnon", "turn_off", "turnoff", "brightness", "bright")
        ):
            lights = (
                find_devices(capability="luminance", location="living_room", semantic=True)
                or find_devices(capability="luminance", semantic=True)
            )
            if lights:
                device = lights[0]
        elif any(token in action_lower for token in ("temp", "temperature", "mode")):
            devices = find_devices(capability="temperature", semantic=True)
            if devices:
                device = devices[0]
    if device is None:
        return {"success": False, "error": f"Device '{device_id}' not found"}

    params = parameters or {}

    # ── Apply state change to local simulation ──
    from ..perception import DeviceStateStore

    DeviceStateStore.initialize()

    action_lower = action.lower()
    if action_lower in ("turnon", "turn_on"):
        DeviceStateStore.set_property(device_id, "on", True)
        if "brightness" in DeviceStateStore.get(device_id):
            DeviceStateStore.set_property(device_id, "brightness", 80)

    elif action_lower in ("turnoff", "turn_off"):
        DeviceStateStore.set_property(device_id, "on", False)
        if "brightness" in DeviceStateStore.get(device_id):
            DeviceStateStore.set_property(device_id, "brightness", 0)

    elif "brightness" in action_lower or "bright" in action_lower:
        level = params.get("level", params.get("brightness", 50))
        DeviceStateStore.set_property(device_id, "on", bool(level > 0))
        DeviceStateStore.set_property(device_id, "brightness", level)

    elif "temp" in action_lower:
        temp = params.get("temp", params.get("temperature", params.get("value", 24)))
        DeviceStateStore.set_property(device_id, "targetTemp", temp)
        DeviceStateStore.set_property(device_id, "on", True)

    elif "mode" in action_lower:
        mode = params.get("mode", "cool")
        DeviceStateStore.set_property(device_id, "mode", mode)

    elif "speed" in action_lower:
        speed = params.get("speed", params.get("level", 1))
        DeviceStateStore.set_property(device_id, "on", bool(speed > 0))
        DeviceStateStore.set_property(device_id, "speed", speed)

    elif "volume" in action_lower:
        vol = params.get("volume", params.get("level", 10))
        DeviceStateStore.set_property(device_id, "volume", vol)

    elif "setMistLevel" in action_lower:
        level = params.get("level", 1)
        DeviceStateStore.set_property(device_id, "on", bool(level > 0))
        DeviceStateStore.set_property(device_id, "mistLevel", level)

    # Emit action event
    bus = get_bus()
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(
                bus.emit(
                    Event(
                        source="wotagent/tools/iot",
                        type="wot.agent.action.started",
                        data={
                            "device": device.title,
                            "device_id": device.device_id,
                            "action": action,
                            "parameters": params,
                            "new_state": DeviceStateStore.get(device_id),
                        },
                    )
                )
            )
    except RuntimeError:
        pass

    return {
        "success": True,
        "device": device.title,
        "device_id": device.device_id,
        "action": action,
        "parameters": params,
        "message": f"Executed {action} on {device.title}",
    }


def _query_device_state(device_id: str) -> dict[str, Any]:
    """Query the current state/properties of a device — local implementation.

    Args:
        device_id: The device identifier.
    """
    from ..wot.td import get_device_by_id

    device = get_device_by_id(device_id)
    if device is None:
        return {"success": False, "error": f"Device '{device_id}' not found"}

    # Inject runtime state from DeviceStateStore
    from ..perception import DeviceStateStore
    runtime_state = DeviceStateStore.get(device_id)

    return {
        "success": True,
        "device": device.title,
        "device_id": device.device_id,
        "capabilities": device.capabilities,
        "location": device.location,
        "properties": {
            p.name: {"type": p.type, "readable": p.readable} for p in device.properties
        },
        "state": runtime_state,
    }


def _rag_retrieve(query: str, k: int = 3) -> list[str]:
    """Search device documentation and Thing Descriptions using RAG.

    Args:
        query: The search query.
        k: Number of results to return (1-10).
    """
    from ..rag import retrieve_td_snippets

    return retrieve_td_snippets(query, k=min(k, 10))


# ---------------------------------------------------------------------------
# MCP-preferring async variants
# ---------------------------------------------------------------------------

_MCP_TIMEOUT_MS = 5000  # 5 s


async def _mcp_list_devices(location: str | None = None) -> list[dict[str, Any]]:
    """List devices — try MCP first, fall back to local TD discovery."""
    try:
        from .mcp_client import get_mcp_client

        client = await get_mcp_client()
        params = {"location": location} if location else {}
        result = await client.call_tool("list_devices", params)
        text = result.get("text", "")
        if text:
            data = json.loads(text)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return _list_devices(location)


async def _mcp_control_device(
    device_id: str,
    action: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Control device — try MCP first, fall back to local DeviceStateStore."""
    try:
        from .mcp_client import get_mcp_client

        client = await get_mcp_client()
        result = await client.call_tool(
            "control_device",
            {
                "device_id": device_id,
                "action": action,
                "parameters": parameters or {},
            },
        )
        text = result.get("text", "")
        if text:
            return json.loads(text)
    except Exception:
        pass
    return _control_device(device_id, action, parameters)


async def _mcp_query_device_state(device_id: str) -> dict[str, Any]:
    """Query device state — try MCP first, fall back to local."""
    try:
        from .mcp_client import get_mcp_client

        client = await get_mcp_client()
        result = await client.call_tool("query_state", {"device_id": device_id})
        text = result.get("text", "")
        if text:
            data = json.loads(text)
            # Re-shape to match original format if needed
            if isinstance(data, dict) and "state" in data:
                state = data["state"]
                from ..wot.td import get_device_by_id

                device = get_device_by_id(device_id)
                if device:
                    return {
                        "success": True,
                        "device": device.title,
                        "device_id": device.device_id,
                        "state": state,
                        "capabilities": device.capabilities,
                        "location": device.location,
                    }
                return {"success": True, "device_id": device_id, "state": state}
            return data
    except Exception:
        pass
    return _query_device_state(device_id)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def get_iot_tools() -> list[StructuredTool]:
    """Return all IoT-related LangChain tools.

    Each tool is registered with both a sync fallback (``func``) and an async
    MCP-preferring variant (``coroutine``).  When used in an async context
    (``ainvoke`` / ``astream_events``) the agent will call the async version,
    which tries to reach the simulation project's MCP server first.
    """
    ac = get_access_control()
    return [
        StructuredTool.from_function(
            func=_list_devices,
            coroutine=_mcp_list_devices,
            name="list_devices",
            description="List all IoT devices with their current runtime state (on/off, brightness, temperature, etc.), optionally filtered by location (parlor, bedroom, study). Returns device ID, title, location, capabilities, actions, and current state values.",
        ),
        StructuredTool.from_function(
            func=_control_device,
            coroutine=_mcp_control_device,
            name="control_device",
            description="Execute an action on an IoT device. Use list_devices first to find device IDs. Example: control_device('light-001', 'turnOn')",
        ),
        StructuredTool.from_function(
            func=_query_device_state,
            coroutine=_mcp_query_device_state,
            name="query_device",
            description="Get detailed info about a specific IoT device including its current runtime state (on/off, brightness, temperature reading, mode, etc.). Requires a valid device_id.",
        ),
        StructuredTool.from_function(
            func=_rag_retrieve,
            name="rag_retrieve",
            description="Search device documentation for relevant information. Use this when you need to find devices matching specific criteria.",
        ),
    ]
