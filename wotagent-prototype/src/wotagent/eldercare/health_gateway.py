"""Scoped MCP Resource client for the Health Agent."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Client

from .mcp_server import build_eldercare_mcp
from .models import HealthToolName, ToolExecutionResult
from .store import EldercareStore


HEALTH_RESOURCE_URIS: dict[HealthToolName, str] = {
    "read_health_summary": "elderly://{resident_id}/health/summary",
    "read_latest_vitals": "elderly://{resident_id}/vitals/latest",
    "read_vital_trend": "elderly://{resident_id}/vitals/trend",
    "check_data_quality": "elderly://{resident_id}/data-quality",
    "read_resident_context": "elderly://{resident_id}/profile",
}


class ScopedHealthMCPClient:
    """Expose only the Health Agent's explicitly registered MCP Resources."""

    def __init__(
        self,
        store: EldercareStore,
        *,
        allowed_tools: set[str] | None = None,
    ) -> None:
        self.store = store
        self.allowed_tools = allowed_tools or set(HEALTH_RESOURCE_URIS)
        self.server = build_eldercare_mcp(store)

    def available_tools(self) -> list[str]:
        return sorted(self.allowed_tools.intersection(HEALTH_RESOURCE_URIS))

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        if tool_name not in self.allowed_tools or tool_name not in HEALTH_RESOURCE_URIS:
            return ToolExecutionResult(
                success=False,
                verified=False,
                tool_name=tool_name,
                arguments=arguments,
                error_code="MCP_SCOPE_DENIED",
                error_message=f"health agent cannot access '{tool_name}'",
            )
        resident_id = str(arguments.get("resident_id", ""))
        if not resident_id:
            return ToolExecutionResult(
                success=False,
                verified=False,
                tool_name=tool_name,
                arguments=arguments,
                error_code="PARAMETER_INVALID",
                error_message="resident_id is required",
            )
        uri = HEALTH_RESOURCE_URIS[tool_name].format(resident_id=resident_id)
        try:
            async with Client(self.server) as client:
                contents = await client.read_resource(uri)
            payload = json.loads(contents[0].text)
            return ToolExecutionResult(
                success=True,
                verified=True,
                tool_name=tool_name,
                arguments={"resident_id": resident_id},
                result=payload,
                observed_state=payload,
            )
        except Exception as exc:
            return ToolExecutionResult(
                success=False,
                verified=False,
                tool_name=tool_name,
                arguments={"resident_id": resident_id},
                error_code="MCP_RESOURCE_FAILED",
                error_message=str(exc),
                retryable=False,
            )


__all__ = ["HEALTH_RESOURCE_URIS", "ScopedHealthMCPClient"]
