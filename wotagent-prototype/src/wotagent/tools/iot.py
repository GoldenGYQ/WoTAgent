"""IoT device control tools — LangChain tools wrapping WoT TD discovery.

Each tool corresponds to a device capability exposed via Thing Descriptions.
Runtime state access goes through MCP to the simulator project.
Static TD metadata (device list, capabilities) is read locally.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from ..auth import get_access_control
from ..events import Event, get_bus
from ..wot.td import find_devices, ThingDescription

# ---------------------------------------------------------------------------
# Tool implementations — sync (TD metadata only, no runtime state)
# ---------------------------------------------------------------------------


def _list_devices(location: str | None = None) -> list[dict[str, Any]]:
    """List all available IoT devices, optionally filtered by location.

    Returns static TD metadata only (no runtime state).
    Use the async variant via MCP to get runtime state.

    Args:
        location: Filter by room (parlor, bedroom, study). Optional.
    """
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


def _control_device(
    device_id: str,
    action: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute an action on a specific IoT device — sync stub.

    The sync variant only validates the device exists via TD and returns a
    placeholder result.  Real state changes happen through the async MCP path.
    """
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

    return {
        "success": True,
        "device": device.title,
        "device_id": device.device_id,
        "action": action,
        "parameters": parameters or {},
        "message": f"Use async MCP path to execute {action} on {device.title}",
        "_via": "sync_stub",
    }


def _query_device_state(device_id: str) -> dict[str, Any]:
    """Query device metadata — sync stub (no runtime state).

    The async MCP path returns full runtime state.
    """
    from ..wot.td import get_device_by_id

    device = get_device_by_id(device_id)
    if device is None:
        return {"success": False, "error": f"Device '{device_id}' not found"}

    return {
        "success": True,
        "device": device.title,
        "device_id": device.device_id,
        "capabilities": device.capabilities,
        "location": device.location,
        "properties": {
            p.name: {"type": p.type, "readable": p.readable} for p in device.properties
        },
        "state": {},
        "_via": "sync_stub",
        "message": "Runtime state available via async MCP path",
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
    """List devices — try MCP first, fall back to local TD discovery.

    MCP version includes runtime state; local fallback returns TD metadata only.
    """
    via = "local_td"
    try:
        from .mcp_client import get_mcp_client

        client = await get_mcp_client()
        params = {"location": location} if location else {}
        result = await client.call_tool("list_devices", params)
        text = result.get("text", "")
        if text:
            data = json.loads(text)
            if isinstance(data, list):
                via = "mcp"
                # Fetch runtime state to enrich the listing
                try:
                    states_result = await client.call_tool("query_all_states", {})
                    states_text = states_result.get("text", "")
                    if states_text:
                        all_states = json.loads(states_text)
                        if isinstance(all_states, dict):
                            for d in data:
                                did = d.get("id", "")
                                if did in all_states:
                                    d["state"] = all_states[did]
                except Exception:
                    logger.debug("MCP state enrichment failed for device listing", exc_info=True)
                for d in data:
                    d["_via"] = via
                return data
    except Exception:
        logger.debug("MCP list_devices failed, using local fallback", exc_info=True)
    result = _list_devices(location)
    for d in result:
        d["_via"] = via
    return result


async def _mcp_control_device(
    device_id: str,
    action: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Control device via MCP. Raises on failure — no local fallback."""
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
        data = json.loads(text)
        data["_via"] = "mcp"
        # Forward events to the agent's EventBus for frontend visibility
        try:
            bus = get_bus()
            await bus.emit(Event(
                source="wotagent/tools",
                type="wot.device.state_changed",
                data={"device_id": device_id, "action": action, "via": "mcp", "result": data},
            ))
        except Exception:
            logger.debug("Failed to emit state_changed event", exc_info=True)
        return data
    return {"success": False, "error": f"MCP returned empty result for {device_id}",
            "_via": "mcp"}


async def _mcp_query_device_state(device_id: str) -> dict[str, Any]:
    """Query device state via MCP. Raises on failure — no local fallback."""
    from .mcp_client import get_mcp_client

    client = await get_mcp_client()
    result = await client.call_tool("query_state", {"device_id": device_id})
    text = result.get("text", "")
    if text:
        data = json.loads(text)
        via = "mcp"
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
                    "_via": via,
                }
            return {"success": True, "device_id": device_id, "state": state, "_via": via}
        return data
    return {"success": False, "error": f"MCP returned empty result for {device_id}",
            "_via": "mcp"}


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def get_iot_tools() -> list[StructuredTool]:
    """Return all IoT-related LangChain tools.

    Each tool registers both a sync function (for TD metadata only) and an
    async MCP-preferring coroutine (for full runtime state + control).
    The agent pipeline uses the async variant via ``astream_events``.
    """
    ac = get_access_control()
    return [
        StructuredTool.from_function(
            func=_list_devices,
            coroutine=_mcp_list_devices,
            name="list_devices",
            description=(
                "List all IoT devices with their current runtime state "
                "(on/off, brightness, temperature, etc.), optionally filtered "
                "by location (parlor, bedroom, study). Returns device ID, title, "
                "location, capabilities, actions, and current state values."
            ),
        ),
        StructuredTool.from_function(
            func=_control_device,
            coroutine=_mcp_control_device,
            name="control_device",
            description=(
                "Execute an action on an IoT device. "
                "Use list_devices first to find device IDs. "
                "Example: control_device('light-001', 'turnOn')"
            ),
        ),
        StructuredTool.from_function(
            func=_query_device_state,
            coroutine=_mcp_query_device_state,
            name="query_device",
            description=(
                "Get detailed info about a specific IoT device including its "
                "current runtime state (on/off, brightness, temperature reading, "
                "mode, etc.). Requires a valid device_id."
            ),
        ),
        StructuredTool.from_function(
            func=_rag_retrieve,
            name="rag_retrieve",
            description=(
                "Search device documentation for relevant information. "
                "Use this when you need to find devices matching specific criteria."
            ),
        ),
    ]
