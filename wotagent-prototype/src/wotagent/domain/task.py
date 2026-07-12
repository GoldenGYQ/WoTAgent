"""Typed contracts shared by planning, policy, execution, and auditing."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    PLANNED = "planned"
    DENIED = "denied"
    WAITING_CONFIRMATION = "waiting_confirmation"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=3)
    delay_ms: int = Field(default=0, ge=0, le=10_000)


class Condition(BaseModel):
    property: str = Field(min_length=1)
    operator: Literal["eq", "ne", "gte", "lte", "in"] = "eq"
    value: Any

    def evaluate(self, state: dict[str, Any]) -> bool:
        if self.property not in state:
            return False
        actual = state[self.property]
        try:
            if self.operator == "eq":
                return actual == self.value
            if self.operator == "ne":
                return actual != self.value
            if self.operator == "gte":
                return actual >= self.value
            if self.operator == "lte":
                return actual <= self.value
            return actual in self.value
        except (TypeError, ValueError):
            return False


class PlanStep(BaseModel):
    id: str = Field(min_length=1)
    kind: Literal["query", "action"]
    device_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    postconditions: list[Condition] = Field(default_factory=list)

    @model_validator(mode="after")
    def infer_standard_postconditions(self) -> "PlanStep":
        """Make verification mandatory for known state-changing WoT actions."""
        if self.kind != "action":
            return self
        inferred: dict[str, Any] | None = None
        if self.action == "turnOn":
            inferred = {"property": "on", "value": True}
        elif self.action == "turnOff":
            inferred = {"property": "on", "value": False}
        elif self.action == "setBrightness" and "brightness" in self.input:
            inferred = {"property": "brightness", "value": self.input["brightness"]}
        elif self.action == "setTemperature" and "temperature" in self.input:
            inferred = {"property": "targetTemp", "value": self.input["temperature"]}
        elif self.action == "setMode" and "mode" in self.input:
            inferred = {"property": "mode", "value": self.input["mode"]}
        elif self.action == "setSpeed" and "speed" in self.input:
            inferred = {"property": "speed", "value": self.input["speed"]}
        elif self.action == "setVolume" and "volume" in self.input:
            inferred = {"property": "volume", "value": self.input["volume"]}
        elif self.action == "setMistLevel" and "level" in self.input:
            inferred = {"property": "mistLevel", "value": self.input["level"]}
        if inferred is not None:
            # Standard action semantics are authoritative. The LLM cannot
            # redefine turnOn as on=false or verify a different parameter.
            self.postconditions = [Condition(**inferred)]
        return self


class TaskPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex[:12]}")
    intent: Literal["chat", "query", "control", "clarify"]
    summary: str = ""
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "TaskPlan":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        known = set(ids)
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(f"step {step.id} has unknown dependencies: {sorted(unknown)}")
            if step.id in step.depends_on:
                raise ValueError(f"step {step.id} cannot depend on itself")

        visiting: set[str] = set()
        visited: set[str] = set()
        deps = {step.id: step.depends_on for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("step dependency graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in deps[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)
        return self


class PolicyDecision(BaseModel):
    effect: Literal["allow", "deny", "confirm"]
    reason_code: str
    reason: str
    risk_level: Literal["low", "medium", "high", "critical"] = "low"


class ExecutionResult(BaseModel):
    step_id: str
    status: Literal["accepted", "failed", "timeout", "skipped"]
    adapter: str = "unknown"
    raw_result: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 1
    error: str | None = None


class VerificationResult(BaseModel):
    step_id: str
    passed: bool
    observed_state: dict[str, Any] = Field(default_factory=dict)
    failed_conditions: list[str] = Field(default_factory=list)


class StepTrace(BaseModel):
    step: PlanStep
    policy: PolicyDecision
    execution: ExecutionResult | None = None
    verification: VerificationResult | None = None

    @property
    def succeeded(self) -> bool:
        if self.execution is None or self.execution.status != "accepted":
            return False
        if self.step.kind == "action":
            return self.verification is not None and self.verification.passed
        return self.verification is None or self.verification.passed


class TaskTrace(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:12]}")
    plan: TaskPlan
    role: str
    status: TaskStatus = TaskStatus.PLANNED
    steps: list[StepTrace] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    error: str | None = None

    def finish(self, status: TaskStatus, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.finished_at = utc_now()
