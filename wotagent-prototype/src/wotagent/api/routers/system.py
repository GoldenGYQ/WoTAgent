from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from ...agentic_config import AgenticSettings
from ...prompting import (
    AGENT_PROMPT_PROFILES,
    CONTEXT_PACK_VERSION,
    PROMPT_KERNEL_VERSION,
)
from ...supervisor import AGENT_REGISTRY
from ..schemas import HealthResponse


def create_system_router(
    *,
    get_pipeline: Callable[[], Any],
    get_settings: Callable[[], AgenticSettings],
    bool_env: Callable[[str, bool], bool],
) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/")
    async def root() -> dict[str, str]:
        return {"name": "WoTAgent 2.0", "status": "ok", "docs": "/docs"}

    @router.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", pipeline="langgraph-v2")

    @router.get("/api/v2/capabilities")
    async def get_capabilities() -> dict[str, Any]:
        settings = get_settings()
        return {
            "thinking": {
                "supported": get_pipeline().thinking_planner is not None,
                "default": False,
                "reasoning_effort": os.getenv("REASONING_EFFORT", "high"),
                "private_reasoning_exposed": False,
            },
            "streaming": bool_env("ENABLE_STREAMING", True),
            "eldercare": {
                "event_driven": True,
                "monitor_process": "wotagent monitor",
                "embedded_monitor": False,
                "event_outbox": True,
                "event_consumer": bool_env(
                    "ELDERCARE_EVENT_CONSUMER_ENABLED",
                    True,
                ),
                "mcp_resources": True,
                "subagents": [
                    "health_agent",
                    "care_agent",
                    "environment_agent",
                ],
                "decision_mode": settings.decision_mode,
                "shadow_mode": settings.shadow_mode,
                "health_agent_loop": {
                    "enabled": settings.decision_mode in {"hybrid", "agentic"},
                    "max_iterations": settings.health_max_iterations,
                    "max_tool_calls": settings.max_tool_calls,
                },
                "butler_goal_loop": {
                    "enabled": settings.decision_mode in {"hybrid", "agentic"},
                    "max_iterations": settings.butler_max_iterations,
                    "fallback": "rule",
                },
                "supervisor_routing": {
                    "strategy": (
                        "rule"
                        if settings.decision_mode == "rule"
                        else "structured_llm"
                    ),
                    "max_routes": settings.supervisor_max_routes,
                    "out_of_scope_return": True,
                    "fallback": "rule",
                },
            },
            "agents": [spec.model_dump() for spec in AGENT_REGISTRY.values()],
        }

    @router.get("/api/v2/agents")
    async def list_domain_agents() -> dict[str, Any]:
        return {"agents": [spec.model_dump() for spec in AGENT_REGISTRY.values()]}

    @router.get("/api/v2/prompts")
    async def list_prompt_profiles() -> dict[str, Any]:
        return {
            "kernel_version": PROMPT_KERNEL_VERSION,
            "context_pack_version": CONTEXT_PACK_VERSION,
            "profiles": [
                profile.model_dump()
                for profile in AGENT_PROMPT_PROFILES.values()
            ],
            "dynamic_context_in_system_prompt": False,
            "response_history_replayed": False,
        }

    return router


__all__ = ["create_system_router"]
