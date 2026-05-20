"""FastAPI application with SSE event streaming.

Agent produces events → EventBus → SSE streaming → Frontend consumes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import logging

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("wotagent.api")


def _enqueue_latest(queue: asyncio.Queue[Event], evt: Event) -> None:
    try:
        queue.put_nowait(evt)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(evt)
        except asyncio.QueueFull:
            pass

# Load .env before any other imports
_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path, encoding="utf-8")

from ..core import create_wot_pipeline, get_session_manager, invoke_agent, invoke_agent_stream, AgentContext
from ..events import Event, get_bus
from ..logging import configure_logging, install_event_bus_handler
from ..memory import SessionTranscript
from ..perception import get_perception_engine
from ..wot import find_devices, load_tds

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

configure_logging()
install_event_bus_handler()

# Create the agent once at startup
_pipeline = create_wot_pipeline()
_session_manager = get_session_manager(timeout_minutes=30)

# Start perception engine
_perception_engine = get_perception_engine(polling_interval=60, auto_start=True)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="WoTAgent",
    description="Web of Things Agent — LangChain-powered IoT control with event streaming",
    version="0.3.0",
)

# Allow Vue dev server (5173) and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    role: str = "operator"


class ChatResponse(BaseModel):
    response: str
    session_id: str


class SessionCreateRequest(BaseModel):
    role: str = "operator"


class SessionInfoResponse(BaseModel):
    session_id: str
    created_at: str
    user_role: str
    message_count: int
    last_active: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "pipeline": "wot_pipeline_main"}

@app.get("/api/diag")
async def diag():
    from ..perception import DeviceStateStore
    DeviceStateStore.initialize()
    device_count = len(DeviceStateStore.get_all())
    return {
        "pipeline": "wot_pipeline_main",
        "device_count": device_count,
        "sessions_active": _session_manager.count,
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint — replaces SSE for real-time chat
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time chat.

    Protocol (JSON messages):

    Client → Server:
      ``{"type": "chat", "id": "cmd-001", "data": {"message": "...", "session_id": "..."}}``
      ``{"type": "ping", "id": "cmd-002"}``

    Server → Client:
      ``{"type": "response", "id": "cmd-001", "success": true, "data": {"session_id": "..."}}``
      ``{"type": "event", "event_type": "wot.agent.thought", "data": {...}, "session_id": "...", "timestamp": ...}``
      ``{"type": "error", "id": "...", "message": "..."}``
      ``{"type": "pong", "id": "cmd-002"}``
    """
    await websocket.accept()
    bus = get_bus()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=200)

    # Subscribe to ALL events from EventBus
    unsub = bus.subscribe(lambda evt: _enqueue_latest(queue, evt))

    # Background task: forward EventBus events → WebSocket
    async def forward_events():
        while True:
            try:
                evt = await queue.get()
                await websocket.send_json({
                    "type": "event",
                    "event_type": evt.type,
                    "data": evt.data,
                    "session_id": evt.session_id,
                    "timestamp": evt.time.timestamp() if evt.time else 0,
                })
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("Skipped bad event %s", getattr(evt, "type", "?"), exc_info=True)
                continue

    forward_task = asyncio.create_task(forward_events())

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            msg_id = data.get("id", "")

            if msg_type == "chat":
                msg_data = data.get("data", {})
                message = msg_data.get("message", "")
                session_id = msg_data.get("session_id", "web_ui")

                session = _session_manager.get_or_create(
                    _pipeline, session_id=session_id, user_role="operator",
                )
                session.memory.load()

                # Launch agent — events flow to EventBus → WebSocket forwarder
                asyncio.create_task(_run_agent_task(session, message))

                await websocket.send_json({
                    "type": "response",
                    "id": msg_id,
                    "success": True,
                    "data": {"session_id": session.session_id},
                })

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong", "id": msg_id})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception:
        logger.exception("WebSocket error")
    finally:
        forward_task.cancel()
        unsub()


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Send a message to the agent.

    Returns immediately with ``session_id``.  Agent events (plan, thinking,
    tool calls, response tokens) are streamed asynchronously via the
    ``/api/events/all`` or ``/api/events/{session_id}`` SSE endpoints.

    The frontend should listen for ``wot.agent.plan``, ``wot.agent.thought``,
    ``wot.agent.action.*``, ``wot.agent.response``, and ``wot.session.ended``
    events on its existing SSE connection.
    """
    session_id = req.session_id or "web_ui"
    session = _session_manager.get_or_create(
        _pipeline,
        session_id=session_id,
        user_role=req.role,
    )
    session.memory.load()

    # Launch agent in background — events flow to EventBus → SSE
    asyncio.create_task(_run_agent_task(session, req.message))

    return {"session_id": session.session_id, "status": "processing"}


async def _run_agent_task(session, message: str) -> None:
    """Run the agent pipeline in background, emitting events to EventBus."""
    try:
        async for _chunk in invoke_agent_stream(
            session.agent,
            message,
            session_id=session.session_id,
            user_role=session.user_role,
            memory=session.memory,
            state=session.state,
        ):
            pass  # all events flow through EventBus inside invoke_agent_stream
    except Exception as exc:
        logger.exception("Background agent task failed")
        bus = get_bus()
        await bus.emit(Event(
            source="wotagent/api",
            type="wot.agent.error",
            data={"error": str(exc)},
            session_id=session.session_id,
        ))


@app.post("/api/session")
async def create_session(req: SessionCreateRequest):
    """Create a new agent session."""
    session = _session_manager.create(_pipeline, user_role=req.role)
    return {"session_id": session.session_id, "role": req.role}


@app.get("/api/sessions")
async def list_sessions():
    """List active sessions."""
    return {"sessions": [s.model_dump() for s in _session_manager.list_active()]}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    _session_manager.delete(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.get("/api/session/{session_id}/messages")
async def session_messages(session_id: str):
    """Load chat message history from session transcript.

    Returns user/assistant message pairs recorded during the session.
    Frontend calls this on page load to restore conversation history.
    """
    entries = SessionTranscript.load_chat(session_id)
    return {"session_id": session_id, "messages": entries}


@app.get("/api/devices")
async def list_devices(location: str | None = None):
    """List IoT devices, optionally filtered by location."""
    devices = find_devices(location=location) if location else find_devices()
    from ..perception import DeviceStateStore
    return {
        "devices": [
            {
                "id": d.device_id,
                "title": d.title,
                "location": d.location,
                "capabilities": d.capabilities,
                "actions": [a.name for a in d.actions],
                "state": DeviceStateStore.get(d.device_id),
            }
            for d in devices
        ]
    }


@app.get("/api/perception/state")
async def perception_state():
    """Current environment state snapshot from the perception engine."""
    from ..perception import DeviceStateStore
    DeviceStateStore.initialize()
    return {
        "engine_running": _perception_engine.is_running,
        "stats": _perception_engine.stats,
        "summary": _perception_engine.get_context(),
        "devices": DeviceStateStore.get_all(),
    }


@app.get("/api/perception/rules")
async def perception_rules():
    """List all perception rules and their status."""
    return {
        "rules": [
            {
                "name": r.name,
                "description": r.description,
                "enabled": r.enabled,
                "condition": {
                    "device_id": r.condition.device_id,
                    "property": r.condition.property_name,
                    "operator": r.condition.operator,
                    "threshold": r.condition.value,
                },
                "ready": r.is_ready(),
            }
            for r in _perception_engine.rules
        ]
    }


@app.post("/api/perception/poll")
async def perception_poll():
    """Trigger a one-shot perception poll cycle."""
    triggered = await _perception_engine.poll_once()
    return {
        "triggered": len(triggered),
        "rules": triggered,
    }


@app.get("/api/events/{session_id}")
async def stream_events(session_id: str):
    """SSE endpoint — streams agent events for a session in real-time.

    The frontend can connect to this endpoint to receive a live feed
    of agent thoughts, actions, observations, and responses.
    """
    bus = get_bus()
    start_cursor = bus.cursor()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)

    # Subscribe to all events for this session
    unsub = bus.subscribe(
        lambda evt: _enqueue_latest(queue, evt) if not evt.session_id or evt.session_id == session_id else None,
    )

    async def event_generator():
        try:
            # Send past events first (replay)
            for evt in bus.history(start_cursor):
                if not evt.session_id or evt.session_id == session_id:
                    yield _format_sse(evt)

            # Stream new events
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if evt.type == "wot.session.ended" and evt.session_id == session_id:
                        yield _format_sse(evt)
                        yield "event: done\ndata: \n\n"
                        break
                    yield _format_sse(evt)
                except asyncio.TimeoutError:
                    # Keep-alive
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsub()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/events/all")
async def stream_all_events():
    """SSE endpoint — streams ALL events from the bus (dashboard use)."""
    bus = get_bus()
    start_cursor = bus.cursor()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=200)

    unsub = bus.subscribe(lambda evt: _enqueue_latest(queue, evt))

    async def event_generator():
        try:
            for evt in bus.history(start_cursor):
                yield _format_sse(evt)
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _format_sse(evt)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsub()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event: Event) -> str:
    """Format an Event as a Server-Sent Event message."""
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"event: {event.type}\ndata: {data}\n\n"
