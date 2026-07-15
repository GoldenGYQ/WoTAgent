"""Device gateway backed by MCP tools generated from WoT TD affordances."""

from __future__ import annotations

from typing import Any

import json

from ..tools.iot import _mcp_list_devices
from ..tools.mcp_client import get_mcp_client


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structured")
    if isinstance(structured, dict):
        return dict(structured)
    text = result.get("text", "")
    parsed = json.loads(text) if text else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class MCPDeviceGateway:
    name = "mcp"

    def __init__(self, client: Any | None = None) -> None:
        self._initialized = False
        self._client = client

    async def _get_client(self):
        return self._client or await get_mcp_client()

    async def initialize(self) -> None:
        if self._initialized:
            return
        client = await self._get_client()
        count = await client.refresh_td_catalog()
        if count == 0:
            raise ConnectionError("MCP server published no TD-generated tools")
        self._initialized = True

    async def capabilities(self) -> list[dict[str, Any]]:
        await self.initialize()
        client = await self._get_client()
        return await client.td_capabilities()

    async def list_devices(self, location: str | None = None) -> list[dict[str, Any]]:
        return await _mcp_list_devices(location)

    async def query_state(self, device_id: str) -> dict[str, Any]:
        await self.initialize()
        client = await self._get_client()
        tool_name, response = await client.call_td_tool(
            device_id=device_id,
            operation="read",
            affordance="state",
        )
        result = _payload(response)
        state = result.get("state", result)
        if isinstance(state, dict):
            state.setdefault("_mcp_tool", tool_name)
        return state if isinstance(state, dict) else {"value": state}

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        await self.initialize()
        client = await self._get_client()
        tool_name, response = await client.call_td_tool(
            device_id=device_id,
            operation="invoke",
            affordance=action,
            arguments=parameters,
        )
        result = _payload(response)
        result["_mcp_tool"] = tool_name
        result.setdefault("idempotency_key", idempotency_key)
        return result
