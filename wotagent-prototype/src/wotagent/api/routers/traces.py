from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from ...telemetry import InMemoryTraceStore
from ..schemas import TraceEventsV2Response


def create_trace_router(
    get_trace_store: Callable[[], InMemoryTraceStore],
) -> APIRouter:
    router = APIRouter(tags=["traces"])

    async def get_task(task_id: str) -> dict[str, Any]:
        record = await get_trace_store().get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="task not found")
        return record.model_dump(mode="json")

    @router.get("/api/v2/tasks/{task_id}")
    async def get_task_route(task_id: str) -> dict[str, Any]:
        return await get_task(task_id)

    @router.get("/api/v2/traces/{trace_id}")
    async def get_trace(trace_id: str) -> dict[str, Any]:
        return await get_task(trace_id)

    @router.get(
        "/api/v2/tasks/{task_id}/events",
        response_model=TraceEventsV2Response,
    )
    async def get_task_events(
        task_id: str,
        after: int = 0,
    ) -> TraceEventsV2Response:
        store = get_trace_store()
        if await store.get(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        events = await store.events(task_id, after=max(after, 0))
        next_cursor = events[-1].sequence if events else max(after, 0)
        return TraceEventsV2Response(
            task_id=task_id,
            events=[event.model_dump(mode="json") for event in events],
            next_cursor=next_cursor,
        )

    @router.get("/api/v2/tasks/{task_id}/events/stream")
    async def stream_task_events(
        task_id: str,
        after: int = 0,
    ) -> StreamingResponse:
        store = get_trace_store()
        if await store.get(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")

        async def generate():
            cursor = max(after, 0)
            while True:
                events = await store.wait_for_events(task_id, after=cursor)
                for event in events:
                    cursor = event.sequence
                    yield (
                        f"id: {cursor}\nevent: {event.type}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                record = await store.get(task_id)
                if record is None or record.status not in {
                    "received",
                    "running",
                    "waiting_confirmation",
                }:
                    break
                if not events:
                    yield ": keep-alive\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    return router


def create_trace_websocket_handler(
    get_trace_store: Callable[[], InMemoryTraceStore],
):
    async def trace_websocket(websocket: WebSocket) -> None:
        store = get_trace_store()
        await websocket.accept()
        try:
            request = await websocket.receive_json()
            task_id = str(request.get("task_id", ""))
            cursor = max(int(request.get("after", 0)), 0)
            if not task_id or await store.get(task_id) is None:
                await websocket.send_json({
                    "type": "error",
                    "error": "task not found",
                })
                await websocket.close(code=4404)
                return
            while True:
                events = await store.wait_for_events(task_id, after=cursor)
                for event in events:
                    cursor = event.sequence
                    await websocket.send_json(event.model_dump(mode="json"))
                record = await store.get(task_id)
                if record is None or record.status not in {
                    "received",
                    "running",
                    "waiting_confirmation",
                }:
                    await websocket.send_json({
                        "type": "stream.completed",
                        "task_id": task_id,
                        "cursor": cursor,
                        "status": record.status if record else "not_found",
                    })
                    return
        except WebSocketDisconnect:
            return
        except (TypeError, ValueError) as exc:
            await websocket.send_json({"type": "error", "error": str(exc)})
            await websocket.close(code=4400)

    return trace_websocket


__all__ = ["create_trace_router", "create_trace_websocket_handler"]
