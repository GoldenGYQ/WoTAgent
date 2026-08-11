"""Device gateway backed by MCP tools generated from WoT TD affordances."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ..tools.mcp_client import get_mcp_client

_active_device_scope: ContextVar[frozenset[str] | None] = ContextVar(
    "wotagent_active_device_scope",
    default=None,
)


@contextmanager
def deployment_device_scope(
    device_ids: list[str] | set[str] | None,
) -> Iterator[None]:
    token = _active_device_scope.set(
        frozenset(device_ids) if device_ids is not None else None
    )
    try:
        yield
    finally:
        _active_device_scope.reset(token)


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structured")
    if isinstance(structured, dict):
        return dict(structured)
    text = result.get("text", "")
    parsed = json.loads(text) if text else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class MCPDeviceGateway:
    name = "mcp"

    def __init__(
        self,
        client: Any | None = None,
        *,
        allowed_scopes: set[str] | None = None,
    ) -> None:
        self._initialized = False
        self._client = client
        self.allowed_scopes = allowed_scopes

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
        capabilities = await client.td_capabilities()
        device_scope = _active_device_scope.get()
        if device_scope is not None:
            capabilities = [
                capability
                for capability in capabilities
                if capability.get("device_id") in device_scope
            ]
        if self.allowed_scopes is None:
            return capabilities
        filtered = []
        for capability in capabilities:
            actions = [
                action for action in capability.get("actions", [])
                if self._scope_allowed(action.get("mcp_scopes", []))
            ]
            readable = self._scope_allowed(capability.get("read_scopes", []))
            if actions or readable:
                filtered.append({
                    **capability,
                    "actions": actions,
                    "properties": capability.get("properties", []) if readable else [],
                })
        return filtered

    async def list_devices(self, location: str | None = None) -> list[dict[str, Any]]:
        capabilities = await self.capabilities()
        devices = []
        for capability in capabilities:
            device_location = capability.get("location")
            if location and device_location != location:
                continue
            devices.append({
                "id": capability.get("device_id", capability.get("id", "")),
                "title": capability.get("title", ""),
                "location": device_location,
                "capabilities": capability.get("capabilities", []),
                "device_type": capability.get("device_type", ""),
                "actions": capability.get("actions", []),
                "properties": capability.get("properties", []),
            })
        return devices

    async def query_state(self, device_id: str) -> dict[str, Any]:
        await self.initialize()
        client = await self._get_client()
        await self._assert_allowed(client, device_id, "read", "state")
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
        await self._assert_allowed(client, device_id, "invoke", action)
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

    def _scope_allowed(self, scopes: list[str]) -> bool:
        return self.allowed_scopes is None or bool(self.allowed_scopes.intersection(scopes))

    async def _assert_allowed(
        self,
        client: Any,
        device_id: str,
        operation: str,
        affordance: str,
    ) -> None:
        capabilities = await client.td_capabilities()
        device_scope = _active_device_scope.get()
        if device_scope is not None and device_id not in device_scope:
            raise PermissionError(
                f"device '{device_id}' is outside active deployment profile"
            )
        if self.allowed_scopes is None:
            return
        device = next(
            (item for item in capabilities if item.get("device_id") == device_id),
            None,
        )
        if device is None:
            raise PermissionError(f"device '{device_id}' is outside MCP scope")
        if operation == "read":
            scopes = device.get("read_scopes", [])
        else:
            action = next(
                (item for item in device.get("actions", []) if item.get("name") == affordance),
                None,
            )
            scopes = action.get("mcp_scopes", []) if action else []
        if not self._scope_allowed(scopes):
            raise PermissionError(
                f"{device_id}.{affordance} is outside MCP scopes {sorted(self.allowed_scopes)}"
            )


__all__ = ["MCPDeviceGateway", "deployment_device_scope"]
