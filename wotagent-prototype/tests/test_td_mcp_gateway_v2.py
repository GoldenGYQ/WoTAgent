"""Tests for MCP client discovery of TD-generated tools."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from wotagent.gateway import mcp as gateway_module
from wotagent.gateway.mcp import MCPDeviceGateway
from wotagent.tools.mcp_client import MCPToolClient


class FakeMCPClient(MCPToolClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "light_001__read_state",
                "meta": {"wot": {
                    "device_id": "light-001",
                    "operation": "read",
                    "affordance": "state",
                }},
            },
            {
                "name": "light_001__action__set_brightness",
                "meta": {"wot": {
                    "device_id": "light-001",
                    "operation": "invoke",
                    "affordance": "setBrightness",
                }},
            },
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        if "read_state" in tool_name:
            return {"structured": {"success": True, "state": {"brightness": 42}}}
        return {"structured": {"success": True, "new_state": {"brightness": 42}}}

    async def refresh_td_catalog(self) -> int:
        return await super().refresh_td_catalog()


def test_gateway_discovers_and_calls_td_generated_tools(monkeypatch) -> None:
    client = FakeMCPClient()

    async def fake_get_client() -> FakeMCPClient:
        return client

    monkeypatch.setattr(gateway_module, "get_mcp_client", fake_get_client)
    gateway = MCPDeviceGateway()

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        action = await gateway.control_device(
            "light-001",
            "setBrightness",
            {"brightness": 42},
            idempotency_key="task:step",
        )
        state = await gateway.query_state("light-001")
        return action, state

    action, state = asyncio.run(run())

    assert client.calls == [
        ("light_001__action__set_brightness", {"brightness": 42}),
        ("light_001__read_state", {}),
    ]
    assert action["_mcp_tool"] == "light_001__action__set_brightness"
    assert state["_mcp_tool"] == "light_001__read_state"


def test_client_rejects_missing_td_affordance() -> None:
    client = FakeMCPClient()
    with pytest.raises(LookupError, match="no TD tool"):
        asyncio.run(client.resolve_td_tool("light-001", "invoke", "turnOff"))
