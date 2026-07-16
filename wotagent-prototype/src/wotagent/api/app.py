"""FastAPI boundary for the WoTAgent 2.0 LangGraph runtime."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from ..agent_graph import (
    AgentGraphDependencies,
    LangChainResponseComposer,
    LangChainTaskPlanner,
    build_agent_graph,
)
from ..conversation import ConversationStore
from ..core import create_wot_pipeline
from ..eldercare import EldercareRuntime, EldercareStore, Resident, SensorReading
from ..gateway import MCPDeviceGateway
from ..logging import configure_logging
from ..orchestration import TaskOrchestrator
from ..policy import PolicyEngine
from ..telemetry import InMemoryTraceStore

_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path, encoding="utf-8")


def _bool_env(key: str, default: bool = True) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


configure_logging()
_pipeline = create_wot_pipeline(
    enable_thinking=_bool_env("ENABLE_THINKING", default=True),
    reasoning_effort=os.getenv("REASONING_EFFORT", "high"),
    enable_streaming=_bool_env("ENABLE_STREAMING", default=True),
)
_v2_policy = PolicyEngine()
_v2_gateway = MCPDeviceGateway()
_v2_trace_store = InMemoryTraceStore()
_v2_conversation_store = ConversationStore(os.getenv("WOTAGENT_CONVERSATION_DB"))
_eldercare_store = EldercareStore(os.getenv("ELDERCARE_DB"))
_eldercare_store.seed_demo()
_eldercare_runtime = EldercareRuntime(_eldercare_store, trace_store=_v2_trace_store)


def _build_v2_graph(checkpointer: Any):
    dependencies = AgentGraphDependencies(
        planner=LangChainTaskPlanner(
            _pipeline.planner,
            trace_store=_v2_trace_store,
            capability_provider=_v2_gateway.capabilities,
            thinking_runnable=_pipeline.thinking_planner,
        ),
        policy=_v2_policy,
        orchestrator=TaskOrchestrator(_v2_gateway, _v2_policy),
        responder=LangChainResponseComposer(
            _pipeline.response_model,
            trace_store=_v2_trace_store,
            thinking_runnable=_pipeline.thinking_response_model,
        ),
        trace_store=_v2_trace_store,
    )
    return build_agent_graph(dependencies, checkpointer=checkpointer)


_v2_graph = _build_v2_graph(InMemorySaver())
_bootstrap_v2_graph = _v2_graph


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _v2_graph
    checkpoint_context = None
    monitor_task = None
    monitor_stop = asyncio.Event()
    previous_graph = _v2_graph
    if _v2_graph is _bootstrap_v2_graph:
        checkpoint_path = Path(os.getenv(
            "WOTAGENT_CHECKPOINT_DB",
            str(Path(__file__).resolve().parents[3] / "data" / "agent_checkpoints.db"),
        ))
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_context = AsyncSqliteSaver.from_conn_string(str(checkpoint_path))
        checkpointer = await checkpoint_context.__aenter__()
        await checkpointer.setup()
        _v2_graph = _build_v2_graph(checkpointer)
    if _bool_env("ELDERCARE_MONITOR_ENABLED", default=True):
        monitor_task = asyncio.create_task(_eldercare_runtime.monitor_forever(
            monitor_stop,
            interval_seconds=float(os.getenv("ELDERCARE_MONITOR_INTERVAL_SECONDS", "5")),
        ))
    try:
        yield
    finally:
        monitor_stop.set()
        if monitor_task is not None:
            await monitor_task
        _v2_graph = previous_graph
        if checkpoint_context is not None:
            await checkpoint_context.__aexit__(None, None, None)


app = FastAPI(
    title="WoTAgent 2.0",
    description="Observable LangGraph agent for TD-described IoT devices",
    version="2.0.0",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5174,http://127.0.0.1:5174,"
        "http://localhost:5175,http://127.0.0.1:5175,http://localhost:8000",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class TaskCreateV2Response(BaseModel):
    task_id: str | None = None
    turn_id: str | None = None
    conversation_id: str
    session_id: str
    status: str
    response: str
    plan: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    confirmation: dict[str, Any] | None = None
    policy_decisions: list[dict[str, Any]] = Field(default_factory=list)
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


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "WoTAgent 2.0", "status": "ok", "docs": "/docs"}


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", pipeline="langgraph-v2")


@app.get("/api/v2/capabilities")
async def get_v2_capabilities() -> dict[str, Any]:
    return {
        "thinking": {
            "supported": _pipeline.thinking_planner is not None,
            "default": False,
            "reasoning_effort": os.getenv("REASONING_EFFORT", "high"),
            "private_reasoning_exposed": False,
        },
        "streaming": _bool_env("ENABLE_STREAMING", default=True),
        "eldercare": {
            "event_driven": True,
            "scheduled_monitor": _bool_env("ELDERCARE_MONITOR_ENABLED", default=True),
            "mcp_resources": True,
            "subagents": ["environment_agent", "care_agent"],
        },
    }


@app.get("/api/v2/eldercare/residents")
async def list_eldercare_residents() -> dict[str, Any]:
    return {
        "residents": [item.model_dump(mode="json") for item in _eldercare_store.list_residents()]
    }


@app.post("/api/v2/eldercare/residents", status_code=201)
async def upsert_eldercare_resident(resident: Resident) -> dict[str, Any]:
    return _eldercare_store.upsert_resident(resident).model_dump(mode="json")


@app.get("/api/v2/eldercare/residents/{resident_id}/summary")
async def get_eldercare_summary(resident_id: str) -> dict[str, Any]:
    try:
        return _eldercare_store.summary(resident_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v2/eldercare/readings", status_code=202)
async def ingest_eldercare_reading(reading: SensorReading) -> dict[str, Any]:
    try:
        return await _eldercare_runtime.ingest(reading)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v2/eldercare/simulate", status_code=202)
async def simulate_eldercare_scenario(req: EldercareScenarioRequest) -> dict[str, Any]:
    try:
        return await _eldercare_runtime.simulate(req.scenario, req.resident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v2/eldercare/events")
async def list_eldercare_events(resident_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    return {
        "events": [
            item.model_dump(mode="json")
            for item in _eldercare_store.list_risks(resident_id, limit=min(max(limit, 1), 500))
        ]
    }


@app.get("/api/v2/eldercare/actions")
async def list_eldercare_actions(resident_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    return {
        "actions": _eldercare_store.list_actions(
            resident_id,
            limit=min(max(limit, 1), 500),
        )
    }


@app.post("/api/v2/conversations", response_model=ConversationV2Response, status_code=201)
async def create_conversation_v2(req: ConversationCreateV2Request) -> dict[str, Any]:
    return _v2_conversation_store.create(
        user_id=req.user_id,
        role=req.role,
        title=req.title,
    )


@app.get("/api/v2/conversations", response_model=ConversationListV2Response)
async def list_conversations_v2(user_id: str = "local-user") -> dict[str, Any]:
    return {"conversations": _v2_conversation_store.list(user_id=user_id)}


@app.get("/api/v2/conversations/{conversation_id}", response_model=ConversationV2Response)
async def get_conversation_v2(conversation_id: str) -> dict[str, Any]:
    conversation = _v2_conversation_store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


@app.get(
    "/api/v2/conversations/{conversation_id}/turns",
    response_model=ConversationTurnsV2Response,
)
async def get_conversation_turns_v2(conversation_id: str) -> dict[str, Any]:
    if _v2_conversation_store.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "conversation_id": conversation_id,
        "turns": _v2_conversation_store.turns(conversation_id),
    }


@app.delete("/api/v2/conversations/{conversation_id}")
async def delete_conversation_v2(conversation_id: str) -> dict[str, str]:
    if not _v2_conversation_store.delete(conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"status": "deleted", "conversation_id": conversation_id}


@app.post("/api/v2/tasks", response_model=TaskCreateV2Response, status_code=202)
async def create_task_v2(
    req: TaskCreateV2Request,
    background_tasks: BackgroundTasks,
) -> TaskCreateV2Response:
    if req.thinking and _pipeline.thinking_planner is None:
        raise HTTPException(status_code=409, detail="thinking mode is not available on this server")
    conversation_id = req.conversation_id or req.session_id
    if conversation_id is None:
        conversation = _v2_conversation_store.create(user_id=req.user_id, role=req.role)
        conversation_id = conversation["conversation_id"]
    elif _v2_conversation_store.get(conversation_id) is None:
        _v2_conversation_store.create(
            conversation_id=conversation_id,
            user_id=req.user_id,
            role=req.role,
        )

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    turn = _v2_conversation_store.start_turn(
        conversation_id=conversation_id,
        task_id=task_id,
        user_message=req.message,
        role=req.role,
    )
    await _v2_trace_store.create(
        task_id=task_id,
        session_id=conversation_id,
        request_id=request_id,
    )
    background_tasks.add_task(
        _run_v2_task,
        task_id=task_id,
        request_id=request_id,
        conversation_id=conversation_id,
        user_id=req.user_id,
        role=req.role,
        message=req.message,
        thinking=req.thinking,
    )
    return TaskCreateV2Response(
        task_id=task_id,
        turn_id=turn["turn_id"],
        conversation_id=conversation_id,
        session_id=conversation_id,
        status="accepted",
        response="",
        thinking=req.thinking,
    )


@app.post("/api/v2/tasks/{task_id}/confirm", response_model=TaskCreateV2Response)
async def confirm_task_v2(task_id: str, req: TaskConfirmV2Request) -> TaskCreateV2Response:
    record = await _v2_trace_store.get(task_id)
    if record is None:
        turn = _v2_conversation_store.turn_for_task(task_id)
        if turn is None:
            raise HTTPException(status_code=404, detail="task not found")
        if turn["status"] != "waiting_confirmation":
            raise HTTPException(status_code=409, detail="task is not waiting for confirmation")
        conversation_id = turn["conversation_id"]
        await _v2_trace_store.create(
            task_id=task_id,
            session_id=conversation_id,
            request_id=f"req_resume_{uuid.uuid4().hex[:8]}",
        )
        await _v2_trace_store.set_status(task_id, "waiting_confirmation")
    else:
        conversation_id = record.session_id

    snapshot = await _v2_graph.aget_state({"configurable": {"thread_id": task_id}})
    if not snapshot.values or not snapshot.interrupts:
        raise HTTPException(status_code=409, detail="task is not waiting for confirmation")
    result = await _v2_graph.ainvoke(
        Command(resume={"approved": req.approved}),
        config={"configurable": {"thread_id": task_id}},
    )
    response = _task_v2_response(result, conversation_id)
    await _finish_v2_trace(response)
    return response


async def _run_v2_task(
    *,
    task_id: str,
    request_id: str,
    conversation_id: str,
    user_id: str,
    role: str,
    message: str,
    thinking: bool,
) -> None:
    await _v2_trace_store.set_status(task_id, "running")
    await _v2_trace_store.record(task_id=task_id, type="task.started", node="api")
    try:
        history = _v2_conversation_store.context(conversation_id, exclude_task_id=task_id)
        runtime_context = _v2_conversation_store.runtime_context(
            conversation_id,
            current_role=role,
            exclude_task_id=task_id,
        )
        await _v2_trace_store.record(
            task_id=task_id,
            type="context.built",
            node="context",
            data={
                "conversation_id": conversation_id,
                "history_messages": len(history),
                "history_characters": sum(len(item["content"]) for item in history),
                "current_role": role,
                "has_pending_goal": runtime_context["pending_goal"] is not None,
                "thinking": thinking,
            },
        )
        result = await _v2_graph.ainvoke({
            "request_id": request_id,
            "task_id": task_id,
            "session_id": conversation_id,
            "user_id": user_id,
            "role": role,
            "message": message,
            "thinking": thinking,
            "conversation_history": history,
            "context": {"runtime": runtime_context},
        }, config={"configurable": {"thread_id": task_id}})
        response = _task_v2_response(result, conversation_id)
        if response.status == "waiting_confirmation":
            await _v2_trace_store.record(
                task_id=task_id,
                type="task.waiting_confirmation",
                node="confirmation",
                data={"confirmation": response.confirmation or {}},
            )
            await _v2_trace_store.set_status(task_id, "waiting_confirmation")
            _v2_conversation_store.finish_turn(
                task_id,
                response=response.response,
                status="waiting_confirmation",
                result=response.model_dump(mode="json"),
            )
        else:
            await _finish_v2_trace(response)
    except Exception as exc:
        await _v2_trace_store.record(
            task_id=task_id,
            type="task.failed",
            node="api",
            data={"error": str(exc)},
        )
        await _v2_trace_store.finish(task_id, status="failed", result={"error": str(exc)})
        _v2_conversation_store.finish_turn(
            task_id,
            response=str(exc),
            status="failed",
            result={"status": "failed", "error": str(exc)},
        )


@app.get("/api/v2/tasks/{task_id}")
async def get_task_v2(task_id: str) -> dict[str, Any]:
    record = await _v2_trace_store.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="task not found")
    return record.model_dump(mode="json")


@app.get("/api/v2/traces/{trace_id}")
async def get_trace_v2(trace_id: str) -> dict[str, Any]:
    return await get_task_v2(trace_id)


@app.get("/api/v2/tasks/{task_id}/events", response_model=TraceEventsV2Response)
async def get_task_events_v2(task_id: str, after: int = 0) -> TraceEventsV2Response:
    if await _v2_trace_store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    events = await _v2_trace_store.events(task_id, after=max(after, 0))
    next_cursor = events[-1].sequence if events else max(after, 0)
    return TraceEventsV2Response(
        task_id=task_id,
        events=[event.model_dump(mode="json") for event in events],
        next_cursor=next_cursor,
    )


@app.get("/api/v2/tasks/{task_id}/events/stream")
async def stream_task_events_v2(task_id: str, after: int = 0) -> StreamingResponse:
    if await _v2_trace_store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")

    async def generate():
        cursor = max(after, 0)
        while True:
            events = await _v2_trace_store.wait_for_events(task_id, after=cursor)
            for event in events:
                cursor = event.sequence
                yield f"id: {cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
            record = await _v2_trace_store.get(task_id)
            if record is None or record.status not in {
                "received", "running", "waiting_confirmation"
            }:
                break
            if not events:
                yield ": keep-alive\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.websocket("/ws/v2")
async def trace_websocket_v2(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        request = await websocket.receive_json()
        task_id = str(request.get("task_id", ""))
        cursor = max(int(request.get("after", 0)), 0)
        if not task_id or await _v2_trace_store.get(task_id) is None:
            await websocket.send_json({"type": "error", "error": "task not found"})
            await websocket.close(code=4404)
            return
        while True:
            events = await _v2_trace_store.wait_for_events(task_id, after=cursor)
            for event in events:
                cursor = event.sequence
                await websocket.send_json(event.model_dump(mode="json"))
            record = await _v2_trace_store.get(task_id)
            if record is None or record.status not in {
                "received", "running", "waiting_confirmation"
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


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, dict) else {}


def _decision_effect(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("effect", ""))
    return str(getattr(value, "effect", ""))


def _task_v2_response(result: dict[str, Any], session_id: str) -> TaskCreateV2Response:
    plan = result.get("plan")
    trace = result.get("trace")
    interrupts = result.get("__interrupt__", [])
    confirmation = interrupts[0].value if interrupts else None
    decisions = result.get("policy_decisions", [])
    if trace is not None:
        status = trace.status.value
    elif confirmation:
        status = "waiting_confirmation"
    elif decisions and all(_decision_effect(item) == "deny" for item in decisions):
        status = "denied"
    elif result.get("error"):
        status = "failed"
    else:
        status = result.get("phase", "unknown")
    task_id = trace.task_id if trace is not None else result.get("task_id")
    turn = _v2_conversation_store.turn_for_task(task_id or "") or {}
    return TaskCreateV2Response(
        task_id=task_id,
        turn_id=turn.get("turn_id"),
        conversation_id=session_id,
        session_id=session_id,
        status=status,
        response=result.get("response", "") or (
            "该操作需要确认后才能执行。" if confirmation else ""
        ),
        plan=_model_dump(plan) if plan is not None else None,
        trace=_model_dump(trace) if trace is not None else None,
        confirmation=confirmation,
        policy_decisions=[_model_dump(item) for item in decisions],
        thinking=bool(result.get("thinking", False)),
    )


async def _finish_v2_trace(response: TaskCreateV2Response) -> None:
    if response.task_id is None:
        return
    await _v2_trace_store.record(
        task_id=response.task_id,
        type=f"task.{response.status}",
        node="api",
        data={"response": response.response},
    )
    payload = response.model_dump(mode="json")
    await _v2_trace_store.finish(response.task_id, status=response.status, result=payload)
    _v2_conversation_store.finish_turn(
        response.task_id,
        response=response.response,
        status=response.status,
        result=payload,
    )
