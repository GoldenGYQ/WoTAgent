"""Core data models for the agent pipeline."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    """Runtime context injected into the agent on every invocation."""
    session_id: str = Field(default="", description="Current session ID")
    user_role: str = Field(default="operator", description="RBAC role")
    user_message: str = Field(default="", description="The raw user message")
    plan_json: str = Field(default="{}", description="Plan from intent classifier or perception rule (JSON)")
    execution_results: str = Field(default="", description="Executor output for observer")
    perception_context: str = Field(default="", description="Current environment state summary")


class PipelinePlan(BaseModel):
    """Structured plan output by the intent-classification stage."""
    intent: str = Field(description="chat | query | control")
    rationale: str = Field(default="", description="Why this plan")
    steps: list[dict[str, Any]] = Field(default_factory=list, description="Action steps")
