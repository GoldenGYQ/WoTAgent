"""Scoped MCP Resource client for the Health Agent."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from fastmcp import Client

from ...orchestration.run_models import ResourceScope
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
HEALTH_TOOL_CAPABILITIES: dict[HealthToolName, str] = {
    "read_health_summary": "health.summary.read",
    "read_latest_vitals": "health.vitals.read",
    "read_vital_trend": "health.trend.read",
    "check_data_quality": "health.data_quality.read",
    "read_resident_context": "resident.context.read",
}


class ScopedHealthMCPClient:
    """Expose only the Health Agent's explicitly registered MCP Resources."""

    def __init__(
        self,
        store: EldercareStore,
        *,
        allowed_tools: set[str] | None = None,
        require_scope: bool = False,
    ) -> None:
        self.store = store
        self.allowed_tools = allowed_tools or set(HEALTH_RESOURCE_URIS)
        self.require_scope = require_scope
        self.server = build_eldercare_mcp(store)
        self._scope: ContextVar[ResourceScope | None] = ContextVar(
            f"health_scope_{id(self)}",
            default=None,
        )

    @contextmanager
    def authorized_scope(self, scope: ResourceScope | None):
        token = self._scope.set(scope)
        try:
            yield
        finally:
            self._scope.reset(token)

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
        scope = self._scope.get()
        resource_id = f"resident:{resident_id}"
        if (
            (self.require_scope and scope is None)
            or (
                scope is not None
                and (
                    resource_id not in scope.resource_ids
                    or (
                        "health.status.read" not in scope.capabilities
                        and HEALTH_TOOL_CAPABILITIES[tool_name]
                        not in scope.capabilities
                    )
                )
            )
        ):
            return ToolExecutionResult(
                success=False,
                verified=False,
                tool_name=tool_name,
                arguments={"resident_id": resident_id},
                error_code="RESOURCE_SCOPE_DENIED",
                error_message=(
                    f"health agent scope does not permit resident "
                    f"'{resident_id}'"
                ),
                retryable=False,
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


__all__ = [
    "HEALTH_RESOURCE_URIS",
    "HEALTH_TOOL_CAPABILITIES",
    "ScopedHealthMCPClient",
]
