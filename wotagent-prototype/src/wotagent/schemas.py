"""Legacy schemas — preserved for backward compatibility.

New code should use the event schema from ``events.schema``.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Objective(BaseModel):
    action: str
    attribute: str
    location: str
    direction: str


class TaskSpec(BaseModel):
    objectives: list[Objective] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    role: str
    capability_domain: str
    device_hint: str | None = None
    operation: str | None = None


class Plan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    steps: list[PlanStep] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    rationale: str | None = None


class ExecutionResult(BaseModel):
    success: bool
    message: str
    applied: list[dict[str, Any]] = Field(default_factory=list)


class EnvironmentEvent(BaseModel):
    state: dict[str, Any]
    rules: list[str]
    triggered: bool
