"""Bounded decision-mode configuration for gradual agent autonomy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


AgentDecisionMode = Literal["rule", "hybrid", "agentic"]


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgenticSettings:
    decision_mode: AgentDecisionMode = "hybrid"
    shadow_mode: bool = False
    health_max_iterations: int = 4
    butler_max_iterations: int = 4
    max_tool_calls: int = 8
    total_timeout_seconds: float = 30.0
    llm_timeout_seconds: float = 12.0
    max_compensations: int = 1

    @classmethod
    def from_env(cls) -> "AgenticSettings":
        mode = os.getenv("AGENT_DECISION_MODE", "hybrid").strip().lower()
        if mode not in {"rule", "hybrid", "agentic"}:
            mode = "hybrid"
        return cls(
            decision_mode=mode,  # type: ignore[arg-type]
            shadow_mode=_bool_env("AGENT_SHADOW_MODE"),
            health_max_iterations=max(1, min(int(os.getenv("HEALTH_AGENT_MAX_ITERATIONS", "4")), 8)),
            butler_max_iterations=max(1, min(int(os.getenv("BUTLER_MAX_ITERATIONS", "4")), 8)),
            max_tool_calls=max(1, min(int(os.getenv("AGENT_MAX_TOOL_CALLS", "8")), 32)),
            total_timeout_seconds=max(1.0, float(os.getenv("AGENT_TOTAL_TIMEOUT_SECONDS", "30"))),
            llm_timeout_seconds=max(1.0, float(os.getenv("AGENT_LLM_TIMEOUT_SECONDS", "12"))),
            max_compensations=max(0, min(int(os.getenv("AGENT_MAX_COMPENSATIONS", "1")), 2)),
        )


__all__ = ["AgentDecisionMode", "AgenticSettings"]
