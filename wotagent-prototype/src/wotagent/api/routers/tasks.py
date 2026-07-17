from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks

from ...application import TaskCreateV2Response
from ...orchestration.task_runner import TaskRunner
from ..schemas import TaskConfirmV2Request, TaskCreateV2Request


def create_task_router(get_runner: Callable[[], TaskRunner]) -> APIRouter:
    router = APIRouter(prefix="/api/v2/tasks", tags=["tasks"])

    @router.post("", response_model=TaskCreateV2Response, status_code=202)
    async def create_task(
        request: TaskCreateV2Request,
        background_tasks: BackgroundTasks,
    ) -> TaskCreateV2Response:
        return await get_runner().create(
            message=request.message,
            conversation_id=request.conversation_id,
            session_id=request.session_id,
            role=request.role,
            user_id=request.user_id,
            thinking=request.thinking,
            background_tasks=background_tasks,
        )

    @router.post("/{task_id}/confirm", response_model=TaskCreateV2Response)
    async def confirm_task(
        task_id: str,
        request: TaskConfirmV2Request,
    ) -> TaskCreateV2Response:
        return await get_runner().confirm(
            task_id=task_id,
            approved=request.approved,
        )

    return router


__all__ = ["create_task_router"]
