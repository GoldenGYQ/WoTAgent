"""Typed contracts for the eldercare monitoring and delegation runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


SensorType = Literal[
    "heart_rate",
    "spo2",
    "fall_detected",
    "bed_occupancy",
    "sleep_quality",
    "room_temperature",
    "room_humidity",
]


class Resident(BaseModel):
    resident_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    room: str = Field(min_length=1)
    caregiver: str = "值班护理员"
    environment_device_id: str | None = None
    wristband_device_id: str | None = None
    mattress_device_id: str | None = None
    environment_sensor_device_id: str | None = None
    active: bool = True


class SensorReading(BaseModel):
    reading_id: str = Field(default_factory=lambda: f"reading_{uuid4().hex[:12]}")
    resident_id: str = Field(min_length=1)
    sensor_type: SensorType
    value: float | bool | str
    unit: str = ""
    source: str = "simulator"
    observed_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"risk_{uuid4().hex[:12]}")
    resident_id: str
    event_type: Literal[
        "high_heart_rate", "low_spo2", "fall_detected", "room_overheat", "user_request"
    ]
    severity: Literal["medium", "high", "critical"]
    value: float | bool
    message: str
    source: str = "health-monitor"
    status: Literal["open", "handled"] = "open"
    occurred_at: datetime = Field(default_factory=utc_now)
    context: dict[str, Any] = Field(default_factory=dict)


class HealthSummary(BaseModel):
    resident: Resident
    latest: dict[str, Any] = Field(default_factory=dict)
    heart_rate_avg: float | None = None
    open_risks: list[RiskEvent] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)


class DelegatedAction(BaseModel):
    name: Literal["adjust_temperature", "notify_caregiver", "create_care_task"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class DelegatedTask(BaseModel):
    delegation_id: str = Field(default_factory=lambda: f"delegation_{uuid4().hex[:10]}")
    agent_id: Literal["environment_agent", "care_agent"]
    objective: str
    resident_scope: list[str]
    allowed_mcp: Literal["eldercare-environment", "eldercare-care"]
    actions: list[DelegatedAction]


class ChildAgentResult(BaseModel):
    delegation_id: str
    agent_id: str
    status: Literal["succeeded", "partial_success", "failed", "denied"]
    actions: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class Delegation(BaseModel):
    agent: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_evidence: list[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=1, le=10)


class GoalPlan(BaseModel):
    goal: str = Field(min_length=1)
    context_summary: str = ""
    delegations: list[Delegation] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list, min_length=1)
    constraints: list[str] = Field(default_factory=list)
    max_iterations: int = Field(default=4, ge=1, le=8)


class Evidence(BaseModel):
    source: str = Field(min_length=1)
    value: Any | None = None
    timestamp: str | None = None
    reliability: float | None = Field(default=None, ge=0, le=1)
    description: str = Field(min_length=1)


class ToolExecutionResult(BaseModel):
    success: bool
    verified: bool
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None
    observed_state: Any | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class AgentFinding(BaseModel):
    agent: str
    objective: str
    status: Literal[
        "completed",
        "needs_more_information",
        "blocked",
        "failed",
    ]
    finding: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    tool_calls: list[ToolExecutionResult] = Field(default_factory=list)


class GoalEvaluation(BaseModel):
    goal_completed: bool
    satisfied_criteria: list[str] = Field(default_factory=list)
    unsatisfied_criteria: list[str] = Field(default_factory=list)
    next_delegations: list[Delegation] = Field(default_factory=list)
    final_reason: str = Field(min_length=1)


HealthToolName = Literal[
    "read_health_summary",
    "read_latest_vitals",
    "read_vital_trend",
    "check_data_quality",
    "read_resident_context",
]


class HealthAgentDecision(BaseModel):
    action: Literal["use_tool", "finish"]
    reason: str = Field(min_length=1)
    tool_name: HealthToolName | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal[
        "completed",
        "needs_more_information",
        "blocked",
        "failed",
    ] = "needs_more_information"
    finding: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)
    recommended_actions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision(self) -> "HealthAgentDecision":
        if self.action == "use_tool" and self.tool_name is None:
            raise ValueError("tool_name is required when action=use_tool")
        if self.action == "finish" and not self.finding:
            raise ValueError("finding is required when action=finish")
        return self
