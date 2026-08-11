"""Dynamic TD and MCP harness for one SimuHome benchmark episode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool

from ..gateway import MCPDeviceGateway
from ..gateway.simuhome import SimuHomeBinding
from ..tools.mcp_client import MCPToolClient
from ..wot import TDAction, TDProperty, ThingDescription, compile_tool_catalog


def capabilities_to_tds(capabilities: list[dict[str, Any]]) -> list[ThingDescription]:
    """Translate Matter-derived capability records into normalized dynamic TDs."""
    return [
        ThingDescription(
            id=f"urn:simuhome:{device['device_id']}",
            title=str(device["title"]),
            location=str(device["location"]),
            capabilities=[str(device.get("device_type", "device"))],
            actions=[
                TDAction(
                    name=str(action["name"]),
                    input_type="object",
                    input_schema=dict(action["input_schema"]),
                    description=f"Invoke {action['name']} through the SimuHome Matter binding.",
                )
                for action in device.get("actions", [])
            ],
            properties=[TDProperty(name=str(name)) for name in device.get("properties", [])],
            raw={
                "id": f"urn:simuhome:{device['device_id']}",
                "title": device["title"],
                "location": device["location"],
                "actions": {
                    action["name"]: {"input": action["input_schema"]}
                    for action in device.get("actions", [])
                },
                "properties": {name: {"type": "string"} for name in device.get("properties", [])},
                "forms": [{"href": "simuhome://runtime", "contentType": "application/json"}],
            },
        )
        for device in capabilities
    ]


def build_mcp_server(
    binding: SimuHomeBinding,
    capabilities: list[dict[str, Any]],
) -> FastMCP:
    """Compile dynamic TDs and register their tools on an in-process MCP server."""
    mcp = FastMCP("wotagent-simuhome")
    by_device = {str(device["device_id"]): device for device in capabilities}
    for definition in compile_tool_catalog(capabilities_to_tds(capabilities)):
        if definition.operation == "read":
            async def handler(_device_id: str = definition.device_id) -> dict[str, Any]:
                return {
                    "success": True,
                    "device_id": _device_id,
                    "state": await binding.query_state(_device_id),
                }
        else:
            async def handler(
                _device_id: str = definition.device_id,
                _action: str = definition.affordance,
                **arguments: Any,
            ) -> dict[str, Any]:
                return await binding.control_device(
                    _device_id,
                    _action,
                    arguments,
                    idempotency_key=f"mcp:{_device_id}:{_action}",
                )

        capability = by_device[definition.device_id]
        mcp.add_tool(FunctionTool(
            name=definition.name,
            description=definition.description,
            parameters=definition.input_schema,
            fn=handler,
            return_type=dict[str, Any],
            run_in_thread=False,
            meta={"wot": {
                "device_id": definition.device_id,
                "operation": definition.operation,
                "affordance": definition.affordance,
                "title": definition.title,
                "location": definition.location,
                "properties": list(definition.properties),
                "device_type": capability.get("device_type", ""),
                "aliases": capability.get("aliases", []),
                "location_aliases": capability.get("location_aliases", []),
                "source": "dynamic-td",
                "binding": "simuhome-rest-matter",
            }},
        ))
    return mcp


@dataclass
class SimuHomeMCPHarness:
    binding: SimuHomeBinding
    gateway: MCPDeviceGateway
    client: MCPToolClient
    tds: list[ThingDescription]

    @classmethod
    async def create(
        cls,
        episode: dict[str, Any],
        message: str,
        *,
        base_url: str = "http://127.0.0.1:18081/api",
        binding: SimuHomeBinding | None = None,
    ) -> SimuHomeMCPHarness:
        runtime_binding = binding or SimuHomeBinding(base_url)
        await runtime_binding.reset_episode(episode)
        runtime_binding.set_request(message)
        capabilities = await runtime_binding.capabilities()
        tds = capabilities_to_tds(capabilities)
        server = build_mcp_server(runtime_binding, capabilities)
        client = MCPToolClient(server=server)
        return cls(
            binding=runtime_binding,
            gateway=MCPDeviceGateway(client=client),
            client=client,
            tds=tds,
        )

    async def close(self) -> None:
        await self.client.disconnect()
        await self.binding.close()


__all__ = ["SimuHomeMCPHarness", "build_mcp_server", "capabilities_to_tds"]
