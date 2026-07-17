from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from wotagent.orchestration import (
    InMemoryRunStore,
    ProposedTask,
    ResourceClaim,
    RunRecord,
    SupervisorTaskPlan,
    TaskScheduler,
)


def _plan(tasks: list[ProposedTask], strategy: str = "parallel_when_safe"):
    return SupervisorTaskPlan(
        goal="完成综合检查",
        execution_strategy=strategy,
        tasks=tasks,
        success_criteria=["所有必要任务有结构化结果"],
    )


def _task(
    local_id: str,
    agent_id: str,
    *,
    depends_on: list[str] | None = None,
    claims: list[ResourceClaim] | None = None,
    parallel_candidate: bool = True,
) -> ProposedTask:
    return ProposedTask(
        local_id=local_id,
        agent_id=agent_id,
        objective=f"执行 {local_id}",
        reason="测试任务",
        success_criteria=[f"{local_id} 完成"],
        depends_on=depends_on or [],
        resource_claims=claims or [],
        parallel_candidate=parallel_candidate,
    )


def _scheduler(*, max_parallel: int = 4) -> TaskScheduler:
    values = iter(["task-a", "task-b", "task-c", "task-d"])
    return TaskScheduler(
        available_agents={"health_agent", "environment_agent", "care_agent"},
        max_parallel_tasks=max_parallel,
        task_id_factory=lambda: next(values),
    )


def test_materialize_assigns_system_ids_and_resolves_dependencies() -> None:
    tasks = _scheduler().materialize("run-1", _plan([
        _task("health", "health_agent"),
        _task("care", "care_agent", depends_on=["health"]),
    ]))

    assert [item.task_id for item in tasks] == ["task-a", "task-b"]
    assert tasks[1].depends_on == ["task-a"]
    assert tasks[0].status == "pending"


def test_independent_reads_can_run_in_one_parallel_batch() -> None:
    tasks = _scheduler().materialize("run-1", _plan([
        _task(
            "health",
            "health_agent",
            claims=[ResourceClaim(key="resident:001", mode="read")],
        ),
        _task(
            "environment",
            "environment_agent",
            claims=[ResourceClaim(key="resident:001", mode="read")],
        ),
    ]))

    batch = _scheduler().next_batch(tasks)

    assert [item.local_id for item in batch] == ["health", "environment"]


def test_scheduler_rejects_conflicting_writes_from_parallel_batch() -> None:
    scheduler = _scheduler()
    tasks = scheduler.materialize("run-1", _plan([
        _task(
            "set-temperature",
            "environment_agent",
            claims=[ResourceClaim(key="device:ac-001", mode="write")],
        ),
        _task(
            "turn-off",
            "environment_agent",
            claims=[ResourceClaim(key="device:ac-001", mode="write")],
        ),
    ]))

    batch = scheduler.next_batch(tasks)

    assert len(batch) == 1
    assert batch[0].local_id == "set-temperature"


def test_dependency_waits_until_parent_is_completed() -> None:
    scheduler = _scheduler()
    tasks = scheduler.materialize("run-1", _plan([
        _task("health", "health_agent"),
        _task("care", "care_agent", depends_on=["health"]),
    ]))

    assert [item.local_id for item in scheduler.next_batch(tasks)] == ["health"]
    tasks[0].status = "completed"
    assert [item.local_id for item in scheduler.next_batch(tasks)] == ["care"]


def test_sequential_strategy_overrides_parallel_candidates() -> None:
    scheduler = _scheduler()
    plan = _plan([
        _task("health", "health_agent"),
        _task("environment", "environment_agent"),
    ], strategy="sequential")
    tasks = scheduler.materialize("run-1", plan)

    batch = scheduler.next_batch(
        tasks,
        execution_strategy=plan.execution_strategy,
    )

    assert [item.local_id for item in batch] == ["health"]


def test_invalid_supervisor_dag_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        _plan([
            _task("a", "health_agent", depends_on=["b"]),
            _task("b", "care_agent", depends_on=["a"]),
        ])


def test_run_store_keeps_run_and_tasks_separate() -> None:
    async def scenario():
        store = InMemoryRunStore()
        run = await store.create_run(RunRecord(
            run_id="run-1",
            conversation_id="conv-1",
            turn_id="turn-1",
            root_goal="检查状态",
        ))
        tasks = _scheduler().materialize("run-1", _plan([
            _task("health", "health_agent"),
            _task("environment", "environment_agent"),
        ]))
        await store.add_tasks(run.run_id, tasks)
        returned = await store.tasks_for_run(run.run_id)
        returned[0].status = "failed"
        stored = await store.tasks_for_run(run.run_id)
        return await store.get_run(run.run_id), stored

    run, stored = asyncio.run(scenario())

    assert run is not None
    assert run.task_ids == ["task-a", "task-b"]
    assert all(item.status == "pending" for item in stored)
