"""Typed contracts for the eldercare monitoring and delegation runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


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
