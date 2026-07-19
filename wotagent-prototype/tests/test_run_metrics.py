from __future__ import annotations

import asyncio

import pytest

from wotagent.orchestration import (
    InMemoryRunStore,
    RunRecord,
    SQLiteRunStore,
)


@pytest.mark.parametrize("durable", [False, True])
def test_run_store_projects_events_into_persisted_metrics(
    durable: bool,
    tmp_path,
) -> None:
    async def scenario():
        store = (
            SQLiteRunStore(tmp_path / "runs.db")
            if durable
            else InMemoryRunStore()
        )
        await store.create_run(RunRecord(
            run_id="run-metrics",
            conversation_id="conv-metrics",
            turn_id="turn-metrics",
            root_goal="检查健康状态和房间环境",
        ))
        await store.record_event(
            "run-metrics",
            "run.started",
            node="run_coordinator",
        )
        await store.record_event(
            "run-metrics",
            "model.request.completed",
            node="butler_run_supervisor",
            iteration=1,
            data={
                "input_tokens": 120,
                "output_tokens": 30,
                "reasoning_tokens": 5,
                "total_tokens": 150,
                "cache_hit_tokens": 20,
                "cache_miss_tokens": 100,
                "estimated_cost": 0.0025,
                "currency": "CNY",
                "pricing_available": True,
                "duration_ms": 450,
            },
        )
        await store.record_event(
            "run-metrics",
            "supervisor.decision.completed",
            node="butler_run_supervisor",
            iteration=1,
        )
        await store.record_event(
            "run-metrics",
            "agent.tool.completed",
            task_id="task-health",
            node="health_agent",
        )
        await store.record_event(
            "run-metrics",
            "agent.resource.completed",
            task_id="task-environment",
            node="environment_agent",
        )
        await store.record_event(
            "run-metrics",
            "agent.loop.completed",
            task_id="task-environment",
            node="environment_agent",
            iteration=1,
        )
        await store.record_event(
            "run-metrics",
            "agent.recovery.completed",
            task_id="task-environment",
            node="environment_agent",
        )
        await store.record_event(
            "run-metrics",
            "run.completed",
            node="run_coordinator",
            data={"response": "检查完成"},
        )
        return await store.get_run("run-metrics")

    run = asyncio.run(scenario())

    assert run is not None
    assert run.usage.model_calls == 1
    assert run.usage.tool_calls == 2
    assert run.usage.input_tokens == 120
    assert run.usage.output_tokens == 30
    assert run.usage.reasoning_tokens == 5
    assert run.usage.total_tokens == 150
    assert run.usage.estimated_cost == pytest.approx(0.0025)
    assert run.usage.pricing_available is True
    assert run.usage.model_latency_ms == pytest.approx(450)
    assert run.usage.ttft_measurements == 0
    assert run.supervisor_iterations == 1
    assert run.agent_loops == 1
    assert run.recoveries == 1
    assert run.duration_ms is not None
    assert run.duration_ms >= 0
    assert run.finished_at is not None


def test_run_store_aggregates_available_ttft_without_fabricating_it() -> None:
    async def scenario():
        store = InMemoryRunStore()
        await store.create_run(RunRecord(
            run_id="run-ttft",
            conversation_id="conv-ttft",
            turn_id="turn-ttft",
            root_goal="介绍系统",
        ))
        await store.record_event(
            "run-ttft",
            "model.request.completed",
            data={
                "total_tokens": 42,
                "duration_ms": 300,
                "ttft_ms": 125,
            },
        )
        return await store.get_run("run-ttft")

    run = asyncio.run(scenario())

    assert run is not None
    assert run.usage.ttft_ms_total == pytest.approx(125)
    assert run.usage.ttft_measurements == 1
