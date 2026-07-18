"""Opt-in real-model smoke tests; never run without explicit acknowledgement."""

from __future__ import annotations

import asyncio
import os

import pytest

from wotagent.orchestration import InMemoryRunStore, RunRecord
from wotagent.runtime import build_run_coordinator, create_runtime_container


ACK = "I_UNDERSTAND_THIS_USES_EXTERNAL_TOKENS"


@pytest.mark.real_model
@pytest.mark.skipif(
    os.getenv("WOTAGENT_REAL_MODEL_SMOKE_ACK") != ACK,
    reason="real-model smoke test requires explicit token-use acknowledgement",
)
def test_real_model_resolves_and_reads_assigned_resident(monkeypatch) -> None:
    async def scenario():
        monkeypatch.setenv("BUTLER_MAX_ITERATIONS", "2")
        monkeypatch.setenv("HEALTH_AGENT_MAX_ITERATIONS", "2")
        monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "4")
        runtime = create_runtime_container(
            enable_thinking=False,
            enable_streaming=False,
        )
        store = InMemoryRunStore()
        await store.create_run(RunRecord(
            run_id="run-real-health-smoke",
            conversation_id="conv-real-health-smoke",
            turn_id="turn-real-health-smoke",
            root_goal="查看张爷爷今天的健康状态",
            user_id="JH-1",
            role="operator",
            status="accepted",
        ))
        coordinator = build_run_coordinator(
            runtime,
            store,
            {
                "user_id": "JH-1",
                "role": "operator",
                "thinking": False,
                "history": [],
                "runtime": {},
            },
            {},
        )
        run = await coordinator.execute(
            run_id="run-real-health-smoke",
            conversation_id="conv-real-health-smoke",
            turn_id="turn-real-health-smoke",
            message="查看张爷爷今天的健康状态",
        )
        return run, await store.tasks_for_run(run.run_id)

    run, tasks = asyncio.run(scenario())

    assert run.status in {"completed", "partial_success"}
    health_tasks = [item for item in tasks if item.agent_id == "health_agent"]
    assert health_tasks
    assert health_tasks[0].target is not None
    assert health_tasks[0].target.id == "elderly-001"
    assert health_tasks[0].effective_scope is not None
    assert health_tasks[0].finding is not None
    assert health_tasks[0].finding.agent == "health_agent"
