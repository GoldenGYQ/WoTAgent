"""FastAPI assembly boundary for the WoTAgent 2.0 runtime."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from ..orchestration.task_runner import TaskRunner, TaskRuntimeAccess
from ..paths import project_root
from ..runtime import create_lifespan, create_runtime_container
from .routers.conversations import create_conversation_router
from .routers.eldercare import create_eldercare_router
from .routers.system import create_system_router
from .routers.tasks import create_task_router
from .routers.traces import create_trace_router, create_trace_websocket_handler

_env_path = project_root() / ".env"
load_dotenv(_env_path, encoding="utf-8")


def _bool_env(key: str, default: bool = True) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_runtime = create_runtime_container(
    enable_thinking=_bool_env("ENABLE_THINKING", default=True),
    enable_streaming=_bool_env("ENABLE_STREAMING", default=True),
)

# Compatibility aliases remain dynamic test seams during the incremental refactor.
_pipeline = _runtime.pipeline
_v2_gateway = _runtime.gateway
_v2_trace_store = _runtime.trace_store
_v2_conversation_store = _runtime.conversation_store
_eldercare_store = _runtime.eldercare_store
_agentic_settings = _runtime.settings
_supervisor_router = _runtime.supervisor_router
_eldercare_runtime = _runtime.eldercare_runtime
_eldercare_chat_agent = _runtime.eldercare_chat_agent


def _build_domain_graphs(checkpointer: Any) -> dict[str, Any]:
    _runtime.pipeline = _pipeline
    _runtime.trace_store = _v2_trace_store
    _runtime.settings = _agentic_settings
    return _runtime.build_domain_graphs(checkpointer)


_domain_graphs = _build_domain_graphs(InMemorySaver())
_v2_graph = _domain_graphs["environment_agent"]
_bootstrap_v2_graph = _v2_graph
_task_graphs: dict[str, Any] = {}


def _set_graphs(primary_graph: Any, domain_graphs: dict[str, Any]) -> None:
    global _v2_graph, _domain_graphs
    _v2_graph = primary_graph
    _domain_graphs = domain_graphs


_lifespan = create_lifespan(
    get_primary_graph=lambda: _v2_graph,
    get_bootstrap_graph=lambda: _bootstrap_v2_graph,
    get_domain_graphs=lambda: _domain_graphs,
    build_domain_graphs=_build_domain_graphs,
    set_graphs=_set_graphs,
    get_eldercare_runtime=lambda: _eldercare_runtime,
    bool_env=lambda key, default: _bool_env(key, default=default),
)

app = FastAPI(
    title="WoTAgent 2.0",
    description="Observable LangGraph agent for TD-described IoT devices",
    version="2.0.0",
    lifespan=_lifespan,
)
app.state.runtime = _runtime
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

_task_runner = TaskRunner(TaskRuntimeAccess(
    get_pipeline=lambda: _pipeline,
    get_trace_store=lambda: _v2_trace_store,
    get_conversation_store=lambda: _v2_conversation_store,
    get_settings=lambda: _agentic_settings,
    get_supervisor_router=lambda: _supervisor_router,
    get_eldercare_store=lambda: _eldercare_store,
    get_eldercare_chat_agent=lambda: _eldercare_chat_agent,
    get_primary_graph=lambda: _v2_graph,
    get_domain_graphs=lambda: _domain_graphs,
    task_graphs=_task_graphs,
))

app.include_router(create_system_router(
    get_pipeline=lambda: _pipeline,
    get_settings=lambda: _agentic_settings,
    bool_env=lambda key, default: _bool_env(key, default=default),
))
app.include_router(create_eldercare_router(
    get_store=lambda: _eldercare_store,
    get_runtime=lambda: _eldercare_runtime,
    get_gateway=lambda: _v2_gateway,
))
app.include_router(create_conversation_router(lambda: _v2_conversation_store))
app.include_router(create_task_router(lambda: _task_runner))
app.include_router(create_trace_router(lambda: _v2_trace_store))
app.websocket("/ws/v2")(create_trace_websocket_handler(
    lambda: _v2_trace_store,
))
