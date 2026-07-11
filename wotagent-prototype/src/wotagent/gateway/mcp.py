"""Adapter over the existing MCP-preferring IoT implementation."""

from __future__ import annotations

from typing import Any

from ..tools.iot import _mcp_control_device, _mcp_list_devices, _mcp_query_device_state


class MCPDeviceGateway:
    name = "mcp"

    async def list_devices(self, location: str | None = None) -> list[dict[str, Any]]:
        return await _mcp_list_devices(location)

    async def query_state(self, device_id: str) -> dict[str, Any]:
        result = await _mcp_query_device_state(device_id)
        state = result.get("state", result)
        return state if isinstance(state, dict) else {"value": state}

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        # The current MCP contract has no idempotency field yet. Keeping the key
        # at this boundary makes adding it to the simulator protocol local.
        result = await _mcp_control_device(device_id, action, parameters)
        result.setdefault("idempotency_key", idempotency_key)
        return result
