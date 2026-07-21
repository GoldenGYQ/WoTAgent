"""Structured cross-Run memory projected from trusted runtime results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GoalMemory(BaseModel):
    source_run_id: str
    goal: str
    status: str
    target_ids: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class EntityMemory(BaseModel):
    type: str
    id: str
    display_name: str | None = None
    source_run_id: str


class VerifiedFactMemory(BaseModel):
    resource_id: str
    value: Any
    source_run_id: str
    source_task_id: str


class ConversationState(BaseModel):
    active_goal: GoalMemory | None = None
    entities: list[EntityMemory] = Field(default_factory=list)
    verified_facts: list[VerifiedFactMemory] = Field(default_factory=list)
    last_outcome: dict[str, Any] | None = None


__all__ = [
    "ConversationState",
    "EntityMemory",
    "GoalMemory",
    "VerifiedFactMemory",
]
