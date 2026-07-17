from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..application import TaskCreateV2Response

class HealthResponse(BaseModel):
    status: str
    pipeline: str


class TaskCreateV2Request(BaseModel):
    message: str
    conversation_id: str | None = None
    session_id: str | None = None
    role: str = "operator"
    user_id: str = "local-user"
    thinking: bool = False


class TaskConfirmV2Request(BaseModel):
    approved: bool


class TraceEventsV2Response(BaseModel):
    task_id: str
    events: list[dict[str, Any]]
    next_cursor: int


class ConversationCreateV2Request(BaseModel):
    user_id: str = "local-user"
    role: str = "operator"
    title: str = "新会话"


class ConversationV2Response(BaseModel):
    conversation_id: str
    user_id: str
    role: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int = 0


class ConversationListV2Response(BaseModel):
    conversations: list[ConversationV2Response]


class ConversationTurnsV2Response(BaseModel):
    conversation_id: str
    turns: list[dict[str, Any]]


class EldercareScenarioRequest(BaseModel):
    resident_id: str = "elderly-001"
    scenario: str


__all__ = [
    "ConversationCreateV2Request",
    "ConversationListV2Response",
    "ConversationTurnsV2Response",
    "ConversationV2Response",
    "EldercareScenarioRequest",
    "HealthResponse",
    "TaskConfirmV2Request",
    "TaskCreateV2Request",
    "TaskCreateV2Response",
    "TraceEventsV2Response",
]
