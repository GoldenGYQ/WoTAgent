from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from ...conversation import ConversationStore
from ..schemas import (
    ConversationCreateV2Request,
    ConversationListV2Response,
    ConversationTurnsV2Response,
    ConversationV2Response,
)


def create_conversation_router(
    get_store: Callable[[], ConversationStore],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/conversations", tags=["conversations"])

    @router.post("", response_model=ConversationV2Response, status_code=201)
    async def create_conversation(
        request: ConversationCreateV2Request,
    ) -> dict[str, Any]:
        return get_store().create(
            user_id=request.user_id,
            role=request.role,
            title=request.title,
        )

    @router.get("", response_model=ConversationListV2Response)
    async def list_conversations(user_id: str = "local-user") -> dict[str, Any]:
        return {"conversations": get_store().list(user_id=user_id)}

    @router.get("/{conversation_id}", response_model=ConversationV2Response)
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        conversation = get_store().get(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation

    @router.get(
        "/{conversation_id}/turns",
        response_model=ConversationTurnsV2Response,
    )
    async def get_conversation_turns(conversation_id: str) -> dict[str, Any]:
        store = get_store()
        if store.get(conversation_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return {
            "conversation_id": conversation_id,
            "turns": store.turns(conversation_id),
        }

    @router.delete("/{conversation_id}")
    async def delete_conversation(conversation_id: str) -> dict[str, str]:
        if not get_store().delete(conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found")
        return {"status": "deleted", "conversation_id": conversation_id}

    return router


__all__ = ["create_conversation_router"]
