"""Single-point tests for ordered trace replay and usage aggregation."""

from __future__ import annotations

import asyncio

from wotagent.telemetry import InMemoryTraceStore


def test_trace_events_are_ordered_and_replayable_from_cursor() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-1", session_id="sess-1", request_id="req-1")
        await store.record(task_id="task-1", type="task.started")
        await store.record(task_id="task-1", type="agent.node.completed", node="planner")
        await store.record(task_id="task-1", type="agent.node.completed", node="policy")
        return await store.events("task-1", after=1)

    events = asyncio.run(run())

    assert [event.sequence for event in events] == [2, 3]
    assert [event.node for event in events] == ["planner", "policy"]


def test_usage_and_loop_metrics_are_aggregated_from_events() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-2", session_id="sess-2", request_id="req-2")
        await store.record(task_id="task-2", type="model.request.completed", data={
            "input_tokens": 100,
            "output_tokens": 20,
            "reasoning_tokens": 30,
            "total_tokens": 150,
            "estimated_cost": 0.02,
            "ttft_ms": 120.0,
        }, duration_ms=400.0)
        await store.record(task_id="task-2", type="agent.tool.completed")
        await store.record(task_id="task-2", type="agent.loop.completed", iteration=2)
        await store.record(task_id="task-2", type="agent.recovery.completed")
        await store.finish(task_id="task-2", status="succeeded", result={"ok": True})
        return await store.get("task-2")

    record = asyncio.run(run())

    assert record.status == "succeeded"
    assert record.usage.model_calls == 1
    assert record.usage.tool_calls == 1
    assert record.usage.total_tokens == 150
    assert record.usage.estimated_cost == 0.02
    assert record.usage.model_latency_ms == 400.0
    assert record.usage.ttft_ms_total == 120.0
    assert record.usage.ttft_measurements == 1
    assert record.loop_count == 2
    assert record.recovery_count == 1


def test_wait_for_events_unblocks_when_an_event_arrives() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-3", session_id="sess-3", request_id="req-3")

        async def publish():
            await asyncio.sleep(0.01)
            await store.record(task_id="task-3", type="task.started")

        publisher = asyncio.create_task(publish())
        events = await store.wait_for_events("task-3", after=0, timeout=1)
        await publisher
        return events

    events = asyncio.run(run())

    assert len(events) == 1
    assert events[0].type == "task.started"
