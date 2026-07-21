"""Scoped MCP Tool client for care coordination actions."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from fastmcp import Client

from ...agents.contracts import ToolExecutionResult
from ...orchestration.run_models import ResourceScope
from .mcp_server import build_eldercare_mcp
from .store import EldercareStore


CARE_TOOLS = {
    "notify_caregiver",
    "notify_duty_caregiver",
    "create_care_task",
}


class ScopedCareMCPClient:
    """Execute care tools only for residents in the attested Task scope."""

    def __init__(
        self,
        store: EldercareStore,
        *,
        allowed_tools: set[str] | None = None,
        require_scope: bool = False,
    ) -> None:
        self.allowed_tools = allowed_tools or set(CARE_TOOLS)
        self.require_scope = require_scope
        self.server = build_eldercare_mcp(store)
        self._scope: ContextVar[ResourceScope | None] = ContextVar(
            f"care_scope_{id(self)}",
            default=None,
        )

    @contextmanager
    def authorized_scope(self, scope: ResourceScope | None):
        token = self._scope.set(scope)
        try:
            yield
        finally:
            self._scope.reset(token)

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        if tool_name not in self.allowed_tools or tool_name not in CARE_TOOLS:
            return self._failure(
                tool_name,
                arguments,
                "MCP_SCOPE_DENIED",
                f"care agent cannot access '{tool_name}'",
            )
        resident_id = str(arguments.get("resident_id", ""))
        if not resident_id:
            return self._failure(
                tool_name,
                arguments,
                "PARAMETER_INVALID",
                "resident_id is required",
            )
        scope = self._scope.get()
        if (
            (self.require_scope and scope is None)
            or (
                scope is not None
                and (
                    f"resident:{resident_id}" not in scope.resource_ids
                    or "care.coordinate" not in scope.capabilities
                )
            )
        ):
            return self._failure(
                tool_name,
                {"resident_id": resident_id},
                "RESOURCE_SCOPE_DENIED",
                f"care agent scope does not permit resident '{resident_id}'",
            )
        try:
            async with Client(self.server) as client:
                response = await client.call_tool(tool_name, arguments)
            payload = dict(response.structured_content or {})
            success = bool(payload.get("success"))
            verified = success and bool(payload.get("verified"))
            return ToolExecutionResult(
                success=success,
                verified=verified,
                tool_name=tool_name,
                arguments=arguments,
                result=payload,
                observed_state=payload if verified else None,
                error_code=None if success else "CARE_ACTION_FAILED",
                error_message=None if success else str(payload),
            )
        except Exception as exc:
            return self._failure(
                tool_name,
                arguments,
                "MCP_TOOL_FAILED",
                str(exc),
            )

    @staticmethod
    def _failure(
        tool_name: str,
        arguments: dict[str, Any],
        code: str,
        message: str,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            success=False,
            verified=False,
            tool_name=tool_name,
            arguments=arguments,
            error_code=code,
            error_message=message,
            retryable=False,
        )


__all__ = ["CARE_TOOLS", "ScopedCareMCPClient"]
