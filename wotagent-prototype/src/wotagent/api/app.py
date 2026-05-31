"""FastAPI application with WebSocket chat and event streaming.

Agent produces events → EventBus → WebSocket for real-time chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import logging

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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

from ..core import create_wot_pipeline, execute_plan_directly, get_session_manager, invoke_agent, invoke_agent_stream, AgentContext
from ..events import Event, get_bus
from ..logging import configure_logging, install_event_bus_handler
from ..memory import ConversationMemory, SessionTranscript
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

# ── Perception auto-execute ────────────────────────────────────────────────
# When a rule triggers, run Executor → Observer automatically.
# Controlled by ENABLE_PERCEPTION_AUTO_EXEC env var (default: false).
_ENABLE_AUTO_EXEC = os.getenv("ENABLE_PERCEPTION_AUTO_EXEC", "false").lower() in ("1", "true", "yes")

if _ENABLE_AUTO_EXEC:
    bus = get_bus()
    _auto_exec_memory = ConversationMemory()  # single memory for all auto-exec turns

    async def _on_rule_triggered(evt: Event) -> None:
        """Handle a perception rule trigger by executing the plan."""
        plan = evt.data.get("plan")
        if not plan:
            return
        await execute_plan_directly(
            _pipeline,
            plan_json=json.dumps(plan, ensure_ascii=False),
            plan=plan,
            session_id="perception_auto",
            user_role="admin",
            memory=_auto_exec_memory,
        )

    _auto_exec_unsub = bus.subscribe(_on_rule_triggered, event_type="wot.perception.rule_triggered")
    logger.info("Perception auto-execution ENABLED (ENABLE_PERCEPTION_AUTO_EXEC=true)")
else:
    _auto_exec_unsub = None
    logger.info("Perception auto-execution DISABLED (default)")
# ────────────────────────────────────────────────────────────────────────────

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


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>WoTAgent</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
    }
    .container { text-align: center; padding: 2rem; max-width: 600px; }
    h1 {
      font-size: 2.5rem;
      font-weight: 700;
      background: linear-gradient(135deg, #38bdf8, #818cf8);
      background-clip: text;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 0.75rem;
    }
    .subtitle { color: #94a3b8; font-size: 1.1rem; margin-bottom: 2rem; }
    .badge {
      display: inline-block;
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      padding: 0.3rem 1rem;
      border-radius: 999px;
      font-size: 0.85rem;
      margin-bottom: 2rem;
    }
    .links { display: flex; flex-direction: column; gap: 0.75rem; }
    a {
      display: flex; align-items: center; justify-content: center; gap: 0.5rem;
      padding: 0.85rem 1.5rem;
      border-radius: 12px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.1);
      color: #e2e8f0;
      text-decoration: none;
      font-size: 1rem;
      transition: all 0.2s;
    }
    a:hover {
      background: rgba(255,255,255,0.1);
      border-color: #38bdf8;
      transform: translateY(-1px);
    }
    .footer { margin-top: 2.5rem; color: #475569; font-size: 0.8rem; }
  </style>
</head>
<body>
  <div class="container">
    <h1>WoTAgent</h1>
    <p class="subtitle">Event-driven IoT Smart-Home Agent</p>
    <div class="badge">&#10003; 服务运行中</div>
    <div class="links">
      <a href="/docs">&#128221; API 文档 (Swagger)</a>
      <a href="/redoc">&#128214; API 文档 (ReDoc)</a>
      <a href="/api/health">&#128154; 健康检查</a>
      <a href="/api/diag">&#128202; 运行诊断</a>
    </div>
    <p class="footer">WoTAgent v0.3.0 &mdash; FastAPI + LangChain + WebSocket</p>
  </div>
</body>
</html>""")


@app.get("/api/health")
async def health():
    return {"status": "ok", "pipeline": "wot_pipeline_main"}

@app.get("/api/diag")
async def diag():
    from ..wot import find_devices
    device_count = len(find_devices())
    # Check MCP connection status
    mcp_status = "unknown"
    try:
        from ..tools.mcp_client import get_mcp_client
        client = await get_mcp_client()
        # Try a lightweight call to check connectivity
        try:
            await client.call_tool("list_devices", {})
            mcp_status = "connected"
        except Exception:
            mcp_status = "disconnected"
    except Exception:
        mcp_status = "disconnected"
    return {
        "pipeline": "wot_pipeline_main",
        "device_count": device_count,
        "sessions_active": _session_manager.count,
        "mcp_status": mcp_status,
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint — primary real-time chat channel
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

                # Launch agent — events flow to EventBus and are forwarded to the WebSocket client
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

    Returns immediately with ``session_id``. Agent events (plan, thinking,
    tool calls, response tokens) are emitted on the EventBus and consumed
    by the primary WebSocket channel.

    The frontend should listen for ``wot.agent.plan``, ``wot.agent.thought``,
    ``wot.agent.action.*``, ``wot.agent.response``, and ``wot.session.ended``
    events on its WebSocket connection when possible.
    """
    session_id = req.session_id or "web_ui"
    session = _session_manager.get_or_create(
        _pipeline,
        session_id=session_id,
        user_role=req.role,
    )
    session.memory.load()

    # Launch agent in background — events flow to EventBus and are picked up by the active transports
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
    # Get runtime state via perception engine (MCP-backed, no direct SQLite)
    all_states: dict[str, dict[str, Any]] = {}
    try:
        all_states = await _perception_engine.get_all_states()
    except Exception:
        pass
    return {
        "devices": [
            {
                "id": d.device_id,
                "title": d.title,
                "location": d.location,
                "capabilities": d.capabilities,
                "actions": [a.name for a in d.actions],
                "state": all_states.get(d.device_id, {}),
            }
            for d in devices
        ]
    }


@app.get("/api/perception/state")
async def perception_state():
    """Current environment state snapshot from the perception engine."""
    all_states: dict[str, dict[str, Any]] = {}
    try:
        all_states = await _perception_engine.get_all_states()
    except Exception:
        pass
    return {
        "engine_running": _perception_engine.is_running,
        "stats": _perception_engine.stats,
        "summary": await _perception_engine.get_context(),
        "devices": all_states,
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


