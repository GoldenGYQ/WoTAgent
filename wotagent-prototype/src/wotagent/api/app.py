"""FastAPI boundary for the WoTAgent 2.0 LangGraph runtime."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from ..eldercare import (
    ELDERCARE_PLAN_SCHEMA,
    EldercareChatAgent,
    EldercareCommandPlanner,
    EldercareRuntime,
    EldercareStore,
    Resident,
    SensorReading,
    is_eldercare_request,
)
from ..gateway import MCPDeviceGateway
from ..logging import configure_logging
from ..orchestration import TaskOrchestrator
from ..policy import PolicyEngine
from ..prompting import (
    AGENT_PROMPT_PROFILES,
    CONTEXT_PACK_VERSION,
    PROMPT_KERNEL_VERSION,
    profile_for,
)
from ..telemetry import InMemoryTraceStore
from ..supervisor import AGENT_REGISTRY, select_supervisor_route

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
_v2_gateway = MCPDeviceGateway()
_v2_trace_store = InMemoryTraceStore()
_v2_conversation_store = ConversationStore(os.getenv("WOTAGENT_CONVERSATION_DB"))
_eldercare_store = EldercareStore(os.getenv("ELDERCARE_DB"))
_eldercare_store.seed_demo()
_eldercare_runtime = EldercareRuntime(
    _eldercare_store,
    trace_store=_v2_trace_store,
    environment_gateway=_v2_gateway,
)
_eldercare_command_runnable = _pipeline.response_model.bind(
    tools=[ELDERCARE_PLAN_SCHEMA],
    tool_choice={
        "type": "function",
        "function": {"name": "generate_eldercare_plan"},
    },
)
_eldercare_chat_agent = EldercareChatAgent(
    _eldercare_runtime,
    EldercareCommandPlanner(
        _eldercare_command_runnable,
        trace_store=_v2_trace_store,
        thinking_runnable=_pipeline.thinking_planner,
    ),
    trace_store=_v2_trace_store,
)


def _build_v2_graph(
    checkpointer: Any,
    *,
    agent_id: str = "environment_agent",
):
    allowed_scopes = set(AGENT_REGISTRY[agent_id].mcp_scopes)
    gateway = MCPDeviceGateway(allowed_scopes=allowed_scopes)
    policy = PolicyEngine()
    dependencies = AgentGraphDependencies(
        planner=LangChainTaskPlanner(
            _pipeline.planner,
            trace_store=_v2_trace_store,
            capability_provider=gateway.capabilities,
            thinking_runnable=_pipeline.thinking_planner,
            agent_profile=profile_for(agent_id),
        ),
        policy=policy,
        orchestrator=TaskOrchestrator(gateway, policy),
        responder=LangChainResponseComposer(
            _pipeline.response_model,
            trace_store=_v2_trace_store,
            thinking_runnable=_pipeline.thinking_response_model,
        ),
        trace_store=_v2_trace_store,
    )
    return build_agent_graph(dependencies, checkpointer=checkpointer)


_DOMAIN_GRAPH_AGENTS = (
    "environment_agent",
    "security_agent",
    "energy_agent",
    "housekeeping_agent",
    "device_ops_agent",
)


def _build_domain_graphs(checkpointer: Any) -> dict[str, Any]:
    return {
        agent_id: _build_v2_graph(
            checkpointer,
            agent_id=agent_id,
        )
        for agent_id in _DOMAIN_GRAPH_AGENTS
    }


_domain_graphs = _build_domain_graphs(InMemorySaver())
_v2_graph = _domain_graphs["environment_agent"]
_bootstrap_v2_graph = _v2_graph
_task_graphs: dict[str, Any] = {}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _v2_graph, _domain_graphs
    checkpoint_context = None
    consumer_task = None
    runtime_stop = asyncio.Event()
    previous_graph = _v2_graph
    previous_domain_graphs = _domain_graphs
    if _v2_graph is _bootstrap_v2_graph:
        checkpoint_path = Path(os.getenv(
            "WOTAGENT_CHECKPOINT_DB",
            str(Path(__file__).resolve().parents[3] / "data" / "agent_checkpoints.db"),
        ))
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_context = AsyncSqliteSaver.from_conn_string(str(checkpoint_path))
        checkpointer = await checkpoint_context.__aenter__()
        await checkpointer.setup()
        _domain_graphs = _build_domain_graphs(checkpointer)
        _v2_graph = _domain_graphs["environment_agent"]
    if _bool_env("ELDERCARE_EVENT_CONSUMER_ENABLED", default=True):
        consumer_task = asyncio.create_task(_eldercare_runtime.consume_outbox_forever(
            runtime_stop,
            interval_seconds=float(os.getenv("ELDERCARE_EVENT_CONSUMER_INTERVAL_SECONDS", "1")),
        ))
    try:
        yield
    finally:
        runtime_stop.set()
        if consumer_task is not None:
            await consumer_task
        _v2_graph = previous_graph
        _domain_graphs = previous_domain_graphs
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
            "monitor_process": "wotagent monitor",
            "embedded_monitor": False,
            "event_outbox": True,
            "event_consumer": _bool_env("ELDERCARE_EVENT_CONSUMER_ENABLED", default=True),
            "mcp_resources": True,
            "subagents": ["health_agent", "care_agent", "environment_agent"],
        },
        "agents": [spec.model_dump() for spec in AGENT_REGISTRY.values()],
    }


@app.get("/api/v2/agents")
async def list_domain_agents() -> dict[str, Any]:
    return {"agents": [spec.model_dump() for spec in AGENT_REGISTRY.values()]}


@app.get("/api/v2/prompts")
async def list_prompt_profiles() -> dict[str, Any]:
    return {
        "kernel_version": PROMPT_KERNEL_VERSION,
        "context_pack_version": CONTEXT_PACK_VERSION,
        "profiles": [
            profile.model_dump()
            for profile in AGENT_PROMPT_PROFILES.values()
        ],
        "dynamic_context_in_system_prompt": False,
        "response_history_replayed": False,
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
        return await _eldercare_runtime.publish_reading(reading)
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


@app.get("/api/v2/eldercare/outbox")
async def list_eldercare_outbox(limit: int = 100) -> dict[str, Any]:
    return {"events": _eldercare_store.list_outbox(limit=limit)}


@app.get("/api/v2/eldercare/monitor/status")
async def get_eldercare_monitor_status() -> dict[str, Any]:
    status = _eldercare_store.service_status("eldercare-monitor")
    return status or {
        "service_id": "eldercare-monitor",
        "status": "offline",
        "reported_status": "unknown",
        "details": {},
        "updated_at": None,
        "age_seconds": None,
    }


@app.post("/api/v2/eldercare/events/{event_id}/acknowledge")
async def acknowledge_eldercare_event(event_id: str) -> dict[str, Any]:
    event = _eldercare_store.handle_risk(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="risk event not found")
    return event.model_dump(mode="json")


@app.get("/api/v2/eldercare/residents/{resident_id}/devices")
async def list_eldercare_devices(resident_id: str) -> dict[str, Any]:
    resident = _eldercare_store.get_resident(resident_id)
    if resident is None:
        raise HTTPException(status_code=404, detail="resident not found")
    mapped_devices = [
        (resident.wristband_device_id, "sensor"),
        (resident.mattress_device_id, "sensor"),
        (resident.environment_sensor_device_id, "sensor"),
        (resident.environment_device_id, "actuator"),
    ]
    devices = []
    try:
        capabilities = await _v2_gateway.list_devices()
    except Exception:
        capabilities = []
    by_id = {str(item.get("id")): item for item in capabilities}
    for device_id, kind in mapped_devices:
        if not device_id:
            continue
        capability = by_id.get(device_id, {})
        try:
            state = await _v2_gateway.query_state(device_id)
            observed_at = state.get("observedAt") or datetime.now(UTC).isoformat()
            devices.append({
                "device_id": device_id,
                "name": capability.get("title") or device_id,
                "kind": kind,
                "transport": "WoT TD / MCP",
                "status": _reading_status(str(observed_at)),
                "last_seen": observed_at,
                "capabilities": [
                    item.get("name") for item in capability.get("actions", [])
                ],
                "state": state,
            })
        except Exception as exc:
            devices.append({
                "device_id": device_id,
                "name": capability.get("title") or device_id,
                "kind": kind,
                "transport": "WoT TD / MCP",
                "status": "offline",
                "last_seen": None,
                "capabilities": [],
                "state": {"error": str(exc)},
            })
    return {"resident_id": resident_id, "devices": devices}


def _reading_status(last_seen: str | None) -> str:
    if not last_seen:
        return "no_data"
    observed_at = datetime.fromisoformat(last_seen)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return "online" if (datetime.now(UTC) - observed_at).total_seconds() <= 600 else "stale"


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

    graph = _task_graphs.get(task_id, _v2_graph)
    snapshot = await graph.aget_state({"configurable": {"thread_id": task_id}})
    if not snapshot.values or not snapshot.interrupts:
        raise HTTPException(status_code=409, detail="task is not waiting for confirmation")
    result = await graph.ainvoke(
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
        supervisor_route = select_supervisor_route(
            message,
            history,
            _eldercare_store.list_residents(),
        )
        await _v2_trace_store.record(
            task_id=task_id,
            type="agent.delegation.created",
            node="butler_supervisor",
            data={
                "agent_id": supervisor_route.agent_id,
                "runtime": supervisor_route.runtime,
                "reason": supervisor_route.reason,
            },
        )
        if supervisor_route.runtime == "eldercare":
            eldercare_result = await _eldercare_chat_agent.run(
                task_id=task_id,
                message=message,
                history=history,
                role=role,
                thinking=thinking,
            )
            if eldercare_result.get("plan") is not None:
                await _v2_trace_store.record(
                    task_id=task_id,
                    type="agent.plan.completed",
                    node="eldercare_planner",
                    data={"plan": eldercare_result["plan"]},
                )
            response = TaskCreateV2Response(
                task_id=task_id,
                turn_id=(
                    _v2_conversation_store.turn_for_task(task_id) or {}
                ).get("turn_id"),
                conversation_id=conversation_id,
                session_id=conversation_id,
                status=str(eldercare_result["status"]),
                response=str(eldercare_result["response"]),
                thinking=thinking,
            )
            await _finish_v2_trace(response)
            return
        graph = _selected_domain_graph(supervisor_route.agent_id)
        _task_graphs[task_id] = graph
        result = await graph.ainvoke({
            "request_id": request_id,
            "task_id": task_id,
            "session_id": conversation_id,
            "user_id": user_id,
            "role": role,
            "message": message,
            "thinking": thinking,
            "conversation_history": history,
            "context": {
                "runtime": runtime_context,
                "delegated_agent": supervisor_route.agent_id,
            },
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


def _selected_domain_graph(agent_id: str):
    environment_graph = _domain_graphs.get("environment_agent")
    if environment_graph is not None and _v2_graph is not environment_graph:
        return _v2_graph
    return _domain_graphs.get(agent_id, _v2_graph)


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
