from __future__ import annotations

import asyncio

from wotagent.agents import AgentFinding
from wotagent.orchestration import (
    PendingConfirmation,
    RunCoordinator,
    RunRecord,
    SQLiteRunStore,
    SupervisorDecision,
    TaskScheduler,
)


def _create_task_decision() -> SupervisorDecision:
    return SupervisorDecision.model_validate({
        "action": "create_tasks",
        "goal": "打开客厅灯",
        "reason": "需要控制环境设备",
        "success_criteria": ["客厅灯为开启状态"],
        "tasks": [{
            "local_id": "turn-on",
            "agent_id": "environment_agent",
            "objective": "打开客厅灯",
            "reason": "用户请求",
            "success_criteria": ["客厅灯为开启状态"],
        }],
    })


def test_sqlite_run_store_survives_new_store_instance(tmp_path) -> None:
    async def scenario():
        path = tmp_path / "runs.db"
        first = SQLiteRunStore(path)
        await first.create_run(RunRecord(
            run_id="run-persist",
            conversation_id="conv-1",
            turn_id="turn-1",
            root_goal="打开客厅灯",
            role="admin",
            user_id="user-1",
            thinking=True,
        ))
        await first.record_event(
            "run-persist",
            "run.started",
            node="test",
        )

        second = SQLiteRunStore(path)
        return (
            await second.get_run("run-persist"),
            await second.events("run-persist"),
        )

    run, events = asyncio.run(scenario())

    assert run is not None
    assert run.role == "admin"
    assert run.user_id == "user-1"
    assert run.thinking is True
    assert [event.type for event in events] == ["run.started"]


def test_raw_trace_artifact_is_persisted_outside_event_projection(
    tmp_path,
) -> None:
    async def scenario():
        path = tmp_path / "runs.db"
        first = SQLiteRunStore(path)
        await first.create_run(RunRecord(
            run_id="run-raw-trace",
            conversation_id="conv-1",
            turn_id="turn-1",
            root_goal="inspect model trace",
        ))
        event = await first.record_event(
            "run-raw-trace",
            "model.request.completed",
            node="supervisor",
            data={"purpose": "supervisor", "total_tokens": 12},
            raw_data={
                "messages": [{"role": "assistant", "content": "full"}],
                "structured_response": {"action": "finish"},
            },
        )
        second = SQLiteRunStore(path)
        artifact = await second.get_trace_artifact(
            str(event.raw_artifact_id)
        )
        return event, artifact

    event, artifact = asyncio.run(scenario())

    assert event.data == {"purpose": "supervisor", "total_tokens": 12}
    assert event.raw_artifact_id is not None
    assert artifact is not None
    assert artifact.event_id == event.event_id
    assert artifact.payload["structured_response"] == {"action": "finish"}


def test_confirmation_resumes_after_store_and_coordinator_restart(
    tmp_path,
) -> None:
    async def scenario():
        path = tmp_path / "runs.db"
        first_store = SQLiteRunStore(path)
        first = RunCoordinator(
            first_store,
            TaskScheduler(
                available_agents={"environment_agent"},
                task_id_factory=lambda: "task-restart",
            ),
            lambda _state: _create_task_decision(),
            {
                "environment_agent": lambda _task: PendingConfirmation(
                    payload={
                        "type": "confirmation_required",
                        "summary": "打开客厅灯",
                    },
                ),
            },
            run_id_factory=lambda: "run-restart",
        )
        waiting = await first.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="打开客厅灯",
        )

        resumed_tasks: list[str] = []

        async def resume(task, approved):
            resumed_tasks.append(task.task_id)
            assert approved is True
            return AgentFinding(
                agent="environment_agent",
                objective=task.objective,
                status="completed",
                finding="客厅灯已打开并验证。",
                confidence=1,
            )

        second_store = SQLiteRunStore(path)
        second = RunCoordinator(
            second_store,
            TaskScheduler(available_agents={"environment_agent"}),
            lambda _state: SupervisorDecision(
                action="finish",
                goal_completed=True,
                goal="打开客厅灯",
                final_response="客厅灯已经打开。",
                reason="设备状态已验证",
            ),
            {},
            resumers={"environment_agent": resume},
        )
        completed = await second.confirm(
            run_id=waiting.run_id,
            task_id="task-restart",
            approved=True,
            message="打开客厅灯",
            context={},
        )
        return (
            waiting,
            completed,
            resumed_tasks,
            await second_store.get_task("task-restart"),
        )

    waiting, completed, resumed_tasks, task = asyncio.run(scenario())

    assert waiting.status == "waiting_confirmation"
    assert completed.status == "completed"
    assert completed.final_response == "客厅灯已经打开。"
    assert resumed_tasks == ["task-restart"]
    assert task is not None
    assert task.status == "completed"
    assert task.confirmation is None


def test_sqlite_store_releases_connection_after_each_operation(tmp_path) -> None:
    path = tmp_path / "close-check.db"

    async def scenario() -> None:
        store = SQLiteRunStore(path)
        await store.create_run(RunRecord(
            run_id="run-close-check",
            conversation_id="conv-close-check",
            turn_id="turn-close-check",
            root_goal="verify database handle lifecycle",
        ))
        await store.record_event(
            "run-close-check",
            "run.started",
            raw_data={"payload": "x" * 1024},
        )

    asyncio.run(scenario())

    # Windows refuses this unlink while any sqlite handle is still open.
    path.unlink()
    assert not path.exists()
