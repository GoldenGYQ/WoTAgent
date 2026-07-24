from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ..paths import data_path


def create_lifespan(
    *,
    get_primary_graph: Callable[[], Any],
    get_bootstrap_graph: Callable[[], Any],
    get_domain_graphs: Callable[[], dict[str, Any]],
    build_domain_graphs: Callable[[Any], dict[str, Any]],
    set_graphs: Callable[[Any, dict[str, Any]], None],
    get_domain_event_consumer: Callable[[], Any],
    bool_env: Callable[[str, bool], bool],
):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        checkpoint_context = None
        consumer_task = None
        runtime_stop = asyncio.Event()
        previous_graph = get_primary_graph()
        previous_domain_graphs = get_domain_graphs()

        if previous_graph is get_bootstrap_graph():
            checkpoint_path = data_path("agent_checkpoints.db")
            configured_path = os.getenv(
                "WOTAGENT_CHECKPOINT_DB",
            )
            if configured_path:
                checkpoint_path = Path(configured_path)
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_context = AsyncSqliteSaver.from_conn_string(
                str(checkpoint_path)
            )
            checkpointer = await checkpoint_context.__aenter__()
            await checkpointer.setup()
            domain_graphs = build_domain_graphs(checkpointer)
            set_graphs(domain_graphs["environment_agent"], domain_graphs)

        consumer_enabled = bool_env(
            "DOMAIN_EVENT_CONSUMER_ENABLED",
            bool_env("ELDERCARE_EVENT_CONSUMER_ENABLED", True),
        )
        if consumer_enabled:
            consumer_task = asyncio.create_task(
                get_domain_event_consumer().run_forever(
                    runtime_stop,
                    interval_seconds=float(os.getenv(
                        "DOMAIN_EVENT_CONSUMER_INTERVAL_SECONDS",
                        os.getenv(
                            "ELDERCARE_EVENT_CONSUMER_INTERVAL_SECONDS",
                            "1",
                        ),
                    )),
                )
            )
        try:
            yield
        finally:
            runtime_stop.set()
            if consumer_task is not None:
                await consumer_task
            set_graphs(previous_graph, previous_domain_graphs)
            if checkpoint_context is not None:
                await checkpoint_context.__aexit__(None, None, None)

    return lifespan


__all__ = ["create_lifespan"]
