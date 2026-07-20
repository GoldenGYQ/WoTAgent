from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    pipeline: str


class RunCreateRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    role: str = "operator"
    user_id: str = "local-user"
    thinking: bool = False
    profile_id: str | None = None
    phone_mode: bool = False


class RunConfirmationRequest(BaseModel):
    approved: bool


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
    turns: list[dict]


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
    "RunCreateRequest",
    "RunConfirmationRequest",
]
