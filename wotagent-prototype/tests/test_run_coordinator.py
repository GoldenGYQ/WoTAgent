from __future__ import annotations

import asyncio
from typing import Any

from wotagent.agents import AgentFinding, Evidence
from wotagent.orchestration import (
    InMemoryRunStore,
    RunCoordinator,
    SupervisorDecision,
    TaskScheduler,
)


def _decision(
    tasks: list[dict[str, Any]],
    *,
    strategy: str = "parallel_when_safe",
) -> SupervisorDecision:
    return SupervisorDecision(
        action="create_tasks",
        goal="综合检查",
        execution_strategy=strategy,
        tasks=tasks,
        success_criteria=["获得全部必要证据"],
        reason="需要专业 Agent 调查",
    )


def _task(local_id: str, agent_id: str, resource: str) -> dict[str, Any]:
    return {
        "local_id": local_id,
        "agent_id": agent_id,
        "objective": f"执行 {local_id}",
        "reason": "测试",
        "success_criteria": [f"{local_id} 完成"],
        "resource_claims": [{"key": resource, "mode": "read"}],
    }


def test_coordinator_executes_independent_agents_concurrently() -> None:
    async def scenario():
        active = 0
        maximum = 0
        lock = asyncio.Lock()

        async def execute(task):
            nonlocal active, maximum
            async with lock:
                active += 1
                maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            return AgentFinding(
                agent=task.agent_id,
                objective=task.objective,
                status="completed",
                finding=f"{task.local_id} completed",
                confidence=1,
                evidence=[Evidence(
                    source="fake",
                    value=True,
                    reliability=1,
                    description="deterministic test evidence",
                )],
            )

        decisions = iter([
            _decision([
                _task("health", "health_agent", "resident:001"),
                _task("environment", "environment_agent", "room:A-101"),
            ]),
            SupervisorDecision(
                action="finish",
                goal="综合检查",
                final_response="综合检查完成",
                reason="证据充分",
            ),
        ])
        store = InMemoryRunStore()
        ids = iter(["task-a", "task-b"])
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"health_agent", "environment_agent"},
                task_id_factory=lambda: next(ids),
            ),
            lambda _context: next(decisions),
            {
                "health_agent": execute,
                "environment_agent": execute,
            },
            run_id_factory=lambda: "run-1",
        )
        run = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="检查健康和环境",
        )
        return (
            run,
            await store.tasks_for_run(run.run_id),
            await store.events(run.run_id),
            maximum,
        )

    run, tasks, events, maximum = asyncio.run(scenario())

    assert run.status == "completed"
    assert run.final_response == "综合检查完成"
    assert maximum == 2
    assert all(task.status == "completed" for task in tasks)
    assert [event.type for event in events] == [
        "run.started",
        "supervisor.decision.completed",
        "task.created",
        "task.created",
        "task.started",
        "task.started",
        "task.completed",
        "task.completed",
        "scheduler.parallel_batch.completed",
        "supervisor.decision.completed",
        "run.completed",
    ]
    assert events[2].task_id == "task-a"


def test_coordinator_serializes_resource_conflicts() -> None:
    async def scenario():
        active = 0
        maximum = 0

        async def execute(task):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return AgentFinding(
                agent=task.agent_id,
                objective=task.objective,
                status="completed",
                finding="completed",
                confidence=1,
            )

        first = _task("set-temperature", "environment_agent", "device:ac-001")
        second = _task("turn-off", "environment_agent", "device:ac-001")
        first["resource_claims"][0]["mode"] = "write"
        second["resource_claims"][0]["mode"] = "write"
        decisions = iter([
            _decision([first, second]),
            SupervisorDecision(
                action="finish",
                goal="设备控制",
                final_response="执行完成",
                reason="任务完成",
            ),
        ])
        ids = iter(["task-a", "task-b"])
        coordinator = RunCoordinator(
            InMemoryRunStore(),
            TaskScheduler(
                available_agents={"environment_agent"},
                task_id_factory=lambda: next(ids),
            ),
            lambda _context: next(decisions),
            {"environment_agent": execute},
            run_id_factory=lambda: "run-1",
        )
        run = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="执行两个冲突动作",
        )
        return run, maximum

    run, maximum = asyncio.run(scenario())

    assert run.status == "completed"
    assert maximum == 1
