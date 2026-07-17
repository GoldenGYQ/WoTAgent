from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, WebSocket, WebSocketDisconnect

from ...application import RunService
from ...orchestration import InMemoryRunStore
from ..schemas import RunCreateRequest


def create_run_router(
    get_service: Callable[[], RunService],
    get_store: Callable[[], InMemoryRunStore],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/runs", tags=["runs"])

    @router.post("", status_code=202)
    async def create_run(
        request: RunCreateRequest,
        background_tasks: BackgroundTasks,
    ):
        return await get_service().create(
            message=request.message,
            conversation_id=request.conversation_id,
            role=request.role,
            user_id=request.user_id,
            thinking=request.thinking,
            background_tasks=background_tasks,
        )

    @router.get("/{run_id}")
    async def get_run(run_id: str):
        return await get_service().require_run(run_id)

    @router.get("/{run_id}/tasks")
    async def get_run_tasks(run_id: str):
        await get_service().require_run(run_id)
        return {"run_id": run_id, "tasks": await get_store().tasks_for_run(run_id)}

    @router.get("/{run_id}/tasks/{task_id}/agent-runs")
    async def get_task_agent_runs(run_id: str, task_id: str):
        await get_service().require_run(run_id)
        task = await get_store().get_task(task_id)
        if task is None or task.run_id != run_id:
            return {"run_id": run_id, "task_id": task_id, "agent_runs": []}
        return {
            "run_id": run_id,
            "task_id": task_id,
            "agent_runs": await get_store().agent_runs_for_task(task_id),
        }

    @router.get("/{run_id}/events")
    async def get_run_events(run_id: str, after: int = 0):
        await get_service().require_run(run_id)
        events = await get_store().events(run_id, after=max(after, 0))
        return {
            "run_id": run_id,
            "events": events,
            "next_cursor": events[-1].sequence if events else max(after, 0),
        }

    return router


def create_run_websocket_handler(
    get_store: Callable[[], InMemoryRunStore],
):
    async def run_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            request = await websocket.receive_json()
            run_id = str(request.get("run_id", ""))
            cursor = max(int(request.get("after", 0)), 0)
            store = get_store()
            if not run_id or await store.get_run(run_id) is None:
                await websocket.send_json({"type": "error", "error": "run not found"})
                await websocket.close(code=4404)
                return
            while True:
                events = await store.wait_for_events(run_id, after=cursor)
                for event in events:
                    cursor = event.sequence
                    await websocket.send_json(event.model_dump(mode="json"))
                run = await store.get_run(run_id)
                if run is None or run.status not in {
                    "accepted",
                    "running",
                    "waiting_confirmation",
                }:
                    await websocket.send_json({
                        "type": "stream.completed",
                        "run_id": run_id,
                        "cursor": cursor,
                        "status": run.status if run else "not_found",
                    })
                    return
        except WebSocketDisconnect:
            return

    return run_websocket


__all__ = ["create_run_router", "create_run_websocket_handler"]
