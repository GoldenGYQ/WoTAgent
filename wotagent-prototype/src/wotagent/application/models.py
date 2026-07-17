from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskCreateV2Response(BaseModel):
    task_id: str | None = None
    turn_id: str | None = None
    conversation_id: str
    session_id: str
    status: str
    response: str
    plan: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    confirmation: dict[str, Any] | None = None
    policy_decisions: list[dict[str, Any]] = Field(default_factory=list)
    thinking: bool = False


__all__ = ["TaskCreateV2Response"]
