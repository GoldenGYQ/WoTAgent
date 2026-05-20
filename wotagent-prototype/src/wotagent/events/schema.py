"""CloudEvents-inspired event schema for agent action tracking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


EventType = Literal[
    "wot.session.started",
    "wot.session.ended",
    "wot.agent.thought",
    "wot.agent.token",
    "wot.agent.plan",
    "wot.agent.action.started",
    "wot.agent.action.completed",
    "wot.agent.action.failed",
    "wot.agent.observation",
    "wot.agent.response",
    "wot.agent.error",
    "wot.device.state_changed",
    "wot.device.discovered",
    "wot.perception.state",
    "wot.perception.rule_triggered",
    "wot.system.log",
]


class Event(BaseModel):
    """CloudEvents 1.0-inspired event envelope.

    Every agent action produces an Event. The event bus delivers these
    to subscribed consumers (logging, SSE, persistence).
    """

    specversion: str = Field(default="1.0", description="CloudEvents spec version")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = Field(..., description="event producer, e.g. wotagent/agent/planner")
    type: EventType = Field(..., description="event type identifier")
    subject: str = Field(default="", description="scoping subject, e.g. session/sess_001")
    time: datetime = Field(default_factory=_utcnow)
    data: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(default="")


class EventBatch(BaseModel):
    """A batch of ordered events for replay or bulk delivery."""

    events: list[Event] = Field(default_factory=list)
    cursor: int = Field(default=0, description="position in the event stream")
