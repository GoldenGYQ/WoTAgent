"""FastAPI assembly boundary for the WoTAgent Run/Task runtime."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from ..application import DomainEventConsumer, RunService
from ..domain import DeploymentProfileRegistry, DomainPluginRegistry
from ..domains.eldercare import EldercareDomainPlugin
from ..domains.smart_home import SmartHomeDomainPlugin
from ..identity import DEMO_PRINCIPALS, PrincipalStore
from ..orchestration import SQLiteRunStore
from ..paths import data_path, project_root
from ..runtime import (
    build_run_coordinator,
    create_lifespan,
    create_runtime_container,
)
from ..speech import SpeechTranscriber
from .routers.conversations import create_conversation_router
from .routers.domains import create_domain_router
from .routers.deployment import create_deployment_router
from .routers.eldercare import create_eldercare_router
from .routers.principals import create_principal_router
from .routers.runs import create_run_router, create_run_websocket_handler
from .routers.speech import create_speech_router
from .routers.system import create_system_router

load_dotenv(project_root() / ".env", encoding="utf-8")


def _bool_env(key: str, default: bool = True) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_runtime = create_runtime_container(
    enable_thinking=_bool_env("ENABLE_THINKING", default=True),
    enable_streaming=_bool_env("ENABLE_STREAMING", default=True),
)
_domain_graphs = _runtime.build_domain_graphs(InMemorySaver())
_bootstrap_graphs = _domain_graphs
_run_store = SQLiteRunStore(
    os.getenv("WOTAGENT_RUN_DB") or data_path("agent_runs.db")
)
_deployment_profiles = DeploymentProfileRegistry.load()
_principal_store = PrincipalStore(
    os.getenv("WOTAGENT_PRINCIPAL_DB") or data_path("principals.db"),
    seed_profiles=DEMO_PRINCIPALS,
)
_run_service = RunService(
    conversation_store=_runtime.conversation_store,
    run_store=_run_store,
    coordinator_factory=lambda context: build_run_coordinator(
        _runtime,
        _run_store,
        context,
        _domain_graphs,
    ),
    thinking_supported=lambda: _runtime.models.thinking_planner is not None,
    deployment_profiles=_deployment_profiles,
    principal_directory=_principal_store,
)
_domain_event_consumer = DomainEventConsumer(
    _runtime.eldercare_store,
    _run_service,
)
_speech_transcriber = SpeechTranscriber()
_domain_registry = DomainPluginRegistry([
    EldercareDomainPlugin(
        get_store=lambda: _runtime.eldercare_store,
        get_runtime=lambda: _runtime.eldercare_runtime,
        get_gateway=lambda: _runtime.gateway,
        principals=_principal_store,
    ),
    SmartHomeDomainPlugin(
        get_gateway=lambda: _runtime.gateway,
        get_profile=lambda: _deployment_profiles.require("villa"),
        principals=_principal_store,
    ),
])


def _build_domain_graphs(checkpointer: Any) -> dict[str, Any]:
    return _runtime.build_domain_graphs(checkpointer)


def _set_graphs(_primary_graph: Any, domain_graphs: dict[str, Any]) -> None:
    global _domain_graphs
    _domain_graphs = domain_graphs


_lifespan = create_lifespan(
    get_primary_graph=lambda: _domain_graphs["environment_agent"],
    get_bootstrap_graph=lambda: _bootstrap_graphs["environment_agent"],
    get_domain_graphs=lambda: _domain_graphs,
    build_domain_graphs=_build_domain_graphs,
    set_graphs=_set_graphs,
    get_domain_event_consumer=lambda: _domain_event_consumer,
    bool_env=lambda key, default: _bool_env(key, default=default),
)

app = FastAPI(
    title="WoTAgent 3.0",
    description="Goal-driven Run/Task DAG over constrained IoT agents",
    version="3.0.0",
    lifespan=_lifespan,
)
app.state.runtime = _runtime
app.state.run_store = _run_store
app.state.run_service = _run_service
app.state.domain_registry = _domain_registry
app.state.deployment_profiles = _deployment_profiles
app.state.principal_store = _principal_store
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5174,http://127.0.0.1:5174,"
        "http://localhost:5175,http://127.0.0.1:5175,"
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(create_system_router(
    get_pipeline=lambda: _runtime.models,
    get_settings=lambda: _runtime.settings,
    bool_env=lambda key, default: _bool_env(key, default=default),
))
app.include_router(create_eldercare_router(
    get_store=lambda: _runtime.eldercare_store,
    get_runtime=lambda: _runtime.eldercare_runtime,
    get_gateway=lambda: _runtime.gateway,
))
app.include_router(create_domain_router(
    lambda: _domain_registry,
    lambda: _principal_store,
    lambda: _run_service,
))
app.include_router(create_deployment_router(
    lambda: _deployment_profiles
))
app.include_router(create_principal_router(
    lambda: _principal_store
))
app.include_router(create_conversation_router(
    lambda: _runtime.conversation_store
))
app.include_router(create_run_router(
    lambda: _run_service,
    lambda: _run_store,
))
app.include_router(create_speech_router(
    lambda: _speech_transcriber
))
app.websocket("/ws/v2")(create_run_websocket_handler(
    lambda: _run_store,
))
