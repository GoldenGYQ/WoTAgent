from __future__ import annotations

import asyncio
from typing import Any

from wotagent.agents import (
    AgentFinding,
    AgentFindingPayload,
    Evidence,
    ToolExecutionResult,
)
from wotagent.orchestration import (
    InMemoryRunStore,
    PendingConfirmation,
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
    assert all(
        task.source_request == "检查健康和环境"
        for task in tasks
    )
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


def test_coordinator_marks_direct_finish_without_tasks_completed() -> None:
    async def scenario():
        store = InMemoryRunStore()
        coordinator = RunCoordinator(
            store,
            TaskScheduler(available_agents={"health_agent"}),
            lambda _context: SupervisorDecision(
                action="finish",
                goal="介绍系统能力",
                final_response="我可以协调健康、照护和设备专业 Agent。",
                reason="一般能力问题无需创建 Task",
            ),
            {},
            run_id_factory=lambda: "run-direct-finish",
        )
        run = await coordinator.execute(
            conversation_id="conv-direct-finish",
            turn_id="turn-direct-finish",
            message="你都能干啥",
        )
        return run, await store.events(run.run_id)

    run, events = asyncio.run(scenario())

    assert run.status == "completed"
    assert run.task_ids == []
    assert run.final_response == "我可以协调健康、照护和设备专业 Agent。"
    assert events[-1].type == "run.completed"


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


def test_confirmation_resumes_same_task_then_returns_to_supervisor() -> None:
    async def scenario():
        resumed: list[bool] = []

        async def resume(_task, approved: bool) -> AgentFinding:
            resumed.append(approved)
            return AgentFinding(
                agent="environment_agent",
                objective="打开客厅灯",
                status="completed",
                finding="客厅灯已打开并验证。",
                confidence=1,
            )

        async def execute(_task):
            return PendingConfirmation(
                payload={
                    "type": "confirmation_required",
                    "summary": "打开客厅灯",
                },
            )

        decisions = iter([
            _decision([{
                **_task(
                    "turn-on",
                    "environment_agent",
                    "device:light-001",
                ),
                "resource_claims": [{
                    "key": "device:light-001",
                    "mode": "write",
                }],
            }]),
            SupervisorDecision(
                action="finish",
                goal="打开客厅灯",
                final_response="客厅灯已经打开。",
                reason="设备状态已验证",
            ),
        ])
        store = InMemoryRunStore()
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"environment_agent"},
                task_id_factory=lambda: "task-confirm",
            ),
            lambda _context: next(decisions),
            {"environment_agent": execute},
            resumers={"environment_agent": resume},
            run_id_factory=lambda: "run-confirm",
        )
        waiting = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="打开客厅灯",
        )
        completed = await coordinator.confirm(
            run_id=waiting.run_id,
            task_id="task-confirm",
            approved=True,
            message="打开客厅灯",
            context={},
        )
        return (
            waiting,
            completed,
            resumed,
            await store.get_task("task-confirm"),
            await store.events("run-confirm"),
        )

    waiting, completed, resumed, task, events = asyncio.run(scenario())

    assert waiting.status == "waiting_confirmation"
    assert completed.status == "completed"
    assert completed.final_response == "客厅灯已经打开。"
    assert resumed == [True]
    assert task is not None and task.status == "completed"
    assert any(
        event.type == "task.confirmation.resolved"
        and event.data["approved"] is True
        for event in events
    )


def test_confirmation_rejection_does_not_resume_device_graph() -> None:
    async def scenario():
        resumed = False

        async def resume(_task, _approved: bool) -> AgentFinding:
            nonlocal resumed
            resumed = True
            raise AssertionError("rejected confirmation must not resume")

        store = InMemoryRunStore()
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"environment_agent"},
                task_id_factory=lambda: "task-reject",
            ),
            lambda _context: _decision([_task(
                "turn-on",
                "environment_agent",
                "device:light-001",
            )]),
            {"environment_agent": lambda _task: PendingConfirmation(
                payload={"summary": "打开客厅灯"},
            )},
            resumers={"environment_agent": resume},
            run_id_factory=lambda: "run-reject",
        )
        waiting = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="打开客厅灯",
        )
        rejected = await coordinator.confirm(
            run_id=waiting.run_id,
            task_id="task-reject",
            approved=False,
            message="打开客厅灯",
            context={},
        )
        return rejected, resumed

    rejected, resumed = asyncio.run(scenario())

    assert rejected.status == "failed"
    assert resumed is False


def test_coordinator_attests_payload_identity_from_runtime() -> None:
    async def scenario():
        decisions = iter([
            _decision([_task(
                "health",
                "health_agent",
                "resident:elderly-001",
            )]),
            SupervisorDecision(
                action="finish",
                goal="健康检查",
                final_response="检查完成",
                reason="证据充分",
            ),
        ])
        store = InMemoryRunStore()
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"health_agent"},
                task_id_factory=lambda: "task-health",
            ),
            lambda _state: next(decisions),
            {
                "health_agent": lambda _task: AgentFindingPayload(
                    status="completed",
                    finding="健康数据已读取。",
                    confidence=1,
                ),
            },
            run_id_factory=lambda: "run-health",
        )
        run = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="查看健康状态",
        )
        task = await store.get_task("task-health")
        return run, task

    run, task = asyncio.run(scenario())

    assert run.status == "completed"
    assert task is not None and task.finding is not None
    assert task.finding.agent == "health_agent"
    assert task.finding.task_id == "task-health"
    assert task.finding.agent_run_id is not None


def test_non_retryable_failure_is_not_executed_twice() -> None:
    async def scenario():
        calls = 0

        async def fail(_task):
            nonlocal calls
            calls += 1
            raise RuntimeError("broken specialist contract")

        decisions = iter([
            _decision([_task("health-1", "health_agent", "resident:001")]),
            _decision([_task("health-2", "health_agent", "resident:001")]),
        ])
        store = InMemoryRunStore()
        ids = iter(["task-1", "task-2"])
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"health_agent"},
                task_id_factory=lambda: next(ids),
            ),
            lambda _state: next(decisions),
            {"health_agent": fail},
            run_id_factory=lambda: "run-repeat",
        )
        run = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="检查健康状态",
        )
        return run, calls, await store.tasks_for_run(run.run_id)

    run, calls, tasks = asyncio.run(scenario())

    assert run.status == "failed"
    assert calls == 1
    assert tasks[0].failure is not None
    assert tasks[0].failure.retryable is False
    assert tasks[1].failure is not None
    assert tasks[1].failure.code == "REPEATED_FAILURE"


def test_failed_payload_becomes_structured_task_failure() -> None:
    async def scenario():
        store = InMemoryRunStore()
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"health_agent"},
                task_id_factory=lambda: "task-scope",
            ),
            lambda _state: _decision([
                _task("health", "health_agent", "resident:001"),
            ]),
            {
                "health_agent": lambda _task: AgentFindingPayload(
                    status="failed",
                    finding="居民范围不允许。",
                    confidence=1,
                    tool_calls=[ToolExecutionResult(
                        success=False,
                        verified=False,
                        tool_name="read_health_summary",
                        error_code="RESOURCE_SCOPE_DENIED",
                        error_message="resident is outside effective scope",
                        retryable=False,
                    )],
                ),
            },
            max_supervisor_iterations=1,
            run_id_factory=lambda: "run-scope",
        )
        await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="查看健康状态",
        )
        return await store.get_task("task-scope")

    task = asyncio.run(scenario())

    assert task is not None
    assert task.failure is not None
    assert task.failure.category == "resource_scope"
    assert task.failure.code == "RESOURCE_SCOPE_DENIED"
    assert task.failure.retryable is False


def test_clarification_after_failed_finding_does_not_mark_run_completed() -> None:
    async def scenario():
        decisions = iter([
            _decision([
                _task("health", "health_agent", "resident:001"),
            ]),
            SupervisorDecision(
                action="clarify",
                goal="检查健康状态",
                clarification="需要更多信息。",
                reason="专业任务失败",
            ),
        ])
        store = InMemoryRunStore()
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"health_agent"},
                task_id_factory=lambda: "task-failed-clarify",
            ),
            lambda _state: next(decisions),
            {
                "health_agent": lambda _task: AgentFindingPayload(
                    status="failed",
                    finding="调查预算已耗尽。",
                    confidence=1,
                ),
            },
            max_supervisor_iterations=2,
            run_id_factory=lambda: "run-failed-clarify",
        )
        return await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="查看健康状态",
        )

    run = asyncio.run(scenario())

    assert run.status == "failed"
    assert run.final_response == "需要更多信息。"


def test_dependent_task_receives_compact_upstream_execution_finding() -> None:
    async def scenario():
        observed_dependency_findings = []

        async def execute(task):
            if task.local_id == "observe":
                return AgentFindingPayload(
                    status="completed",
                    finding="7 盏灯均为关闭状态",
                    confidence=1,
                    evidence=[Evidence(
                        source="device_ops_agent.execution_trace",
                        value={
                            "task_id": task.task_id,
                            "steps": [{
                                "step": {
                                    "id": f"read-{index}",
                                    "kind": "query",
                                    "device_id": f"light-{index:03d}",
                                    "action": "read_state",
                                },
                                "execution": {
                                    "status": "accepted",
                                    "raw_result": {"on": False},
                                    "error": None,
                                },
                                "verification": None,
                                "unrelated_large_field": "x" * 5000,
                            } for index in range(1, 8)],
                        },
                        reliability=1,
                        description="verified device observations",
                    )],
                )
            observed_dependency_findings.extend(task.dependency_findings)
            return AgentFindingPayload(
                status="completed",
                finding="已根据上游偶数结果打开加湿器",
                confidence=1,
            )

        observe = _task("observe", "device_ops_agent", "lights")
        decide = _task("decide", "environment_agent", "humidifier")
        decide["depends_on"] = ["observe"]
        decisions = iter([
            _decision([observe, decide], strategy="sequential"),
            SupervisorDecision(
                action="finish",
                goal="条件控制",
                final_response="条件控制完成",
                reason="上游观察和下游动作均完成",
            ),
        ])
        task_ids = iter(["task-observe", "task-decide"])
        store = InMemoryRunStore()
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={
                    "device_ops_agent",
                    "environment_agent",
                },
                task_id_factory=lambda: next(task_ids),
            ),
            lambda _state: next(decisions),
            {
                "device_ops_agent": execute,
                "environment_agent": execute,
            },
            run_id_factory=lambda: "run-dependency-data",
        )
        run = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="根据灯的数量执行条件控制",
        )
        return run, observed_dependency_findings, await store.events(run.run_id)

    run, dependency_findings, events = asyncio.run(scenario())

    assert run.status == "completed"
    assert len(dependency_findings) == 1
    upstream = dependency_findings[0]
    assert upstream.task_id == "task-observe"
    assert upstream.agent_id == "device_ops_agent"
    assert upstream.status == "completed"
    assert len(upstream.observations) == 7
    assert all(
        observation.result == {"on": False}
        for observation in upstream.observations
    )
    started = [
        event for event in events
        if event.type == "task.started"
        and event.task_id == "task-decide"
    ][0]
    assert started.data["dependency_task_ids"] == ["task-observe"]
    assert started.data["dependency_finding_count"] == 1
