"""Run lifecycle coordinator over a Supervisor, Scheduler, and specialists."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..agents.contracts import AgentFinding
from .run_models import AgentTask, RunRecord, SupervisorDecision
from .run_store import InMemoryRunStore
from .scheduler import TaskScheduler


SupervisorDecider = Callable[
    [dict[str, Any]],
    SupervisorDecision | Awaitable[SupervisorDecision],
]
TaskExecutor = Callable[
    [AgentTask],
    AgentFinding | Awaitable[AgentFinding],
]


class RunCoordinator:
    def __init__(
        self,
        store: InMemoryRunStore,
        scheduler: TaskScheduler,
        supervisor: SupervisorDecider,
        executors: dict[str, TaskExecutor],
        *,
        max_supervisor_iterations: int = 4,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self.supervisor = supervisor
        self.executors = executors
        self.max_supervisor_iterations = max_supervisor_iterations
        self.run_id_factory = run_id_factory or (
            lambda: f"run_{uuid4().hex[:12]}"
        )

    async def execute(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> RunRecord:
        run = await self.store.create_run(RunRecord(
            run_id=self.run_id_factory(),
            conversation_id=conversation_id,
            turn_id=turn_id,
            root_goal=message,
            status="running",
        ))
        findings: list[AgentFinding] = []
        for supervisor_iteration in range(1, self.max_supervisor_iterations + 1):
            decision = await _resolve(self.supervisor({
                "run": run.model_dump(mode="json"),
                "message": message,
                "context": context or {},
                "findings": [
                    finding.model_dump(mode="json") for finding in findings
                ],
                "supervisor_iteration": supervisor_iteration,
            }))
            decision = SupervisorDecision.model_validate(decision)
            if decision.action == "clarify":
                return await self._finish_run(
                    run.run_id,
                    status="completed",
                    response=decision.clarification,
                )
            if decision.action == "finish":
                return await self._finish_run(
                    run.run_id,
                    status=_completion_status(findings),
                    response=decision.final_response,
                )

            plan = decision.task_plan()
            tasks = self.scheduler.materialize(run.run_id, plan)
            await self.store.add_tasks(run.run_id, tasks)
            await self.store.update_run(
                run.run_id,
                lambda item: _apply_plan(item, plan),
            )
            wave_findings = await self._execute_wave(
                run.run_id,
                plan.execution_strategy,
            )
            findings.extend(wave_findings)
            run = await self.store.get_run(run.run_id) or run

        return await self._finish_run(
            run.run_id,
            status=_completion_status(findings),
            response=(
                "Supervisor 达到最大决策轮次，已返回当前任务结果。"
            ),
        )

    async def _execute_wave(
        self,
        run_id: str,
        execution_strategy: str,
    ) -> list[AgentFinding]:
        findings: list[AgentFinding] = []
        while True:
            tasks = await self.store.tasks_for_run(run_id)
            active = [
                item for item in tasks
                if item.status in {"pending", "ready", "running"}
            ]
            if not active:
                return findings
            batch = self.scheduler.next_batch(
                tasks,
                execution_strategy=execution_strategy,
            )
            if not batch:
                await self._block_unreachable_tasks(tasks)
                return findings
            results = await asyncio.gather(*[
                self._execute_task(task) for task in batch
            ])
            findings.extend(results)

    async def _execute_task(self, task: AgentTask) -> AgentFinding:
        await self.store.update_task(
            task.task_id,
            lambda item: setattr(item, "status", "running"),
        )
        executor = self.executors.get(task.agent_id)
        if executor is None:
            finding = _failed_finding(
                task,
                f"no executor registered for {task.agent_id}",
            )
        else:
            try:
                finding = AgentFinding.model_validate(
                    await _resolve(executor(task))
                )
            except Exception as exc:
                finding = _failed_finding(task, str(exc))
        status = "completed" if finding.status == "completed" else "failed"

        def complete(item: AgentTask) -> None:
            item.status = status
            item.finding = finding

        await self.store.update_task(task.task_id, complete)
        return finding

    async def _block_unreachable_tasks(
        self,
        tasks: list[AgentTask],
    ) -> None:
        by_id = {item.task_id: item for item in tasks}
        for task in tasks:
            if task.status not in {"pending", "ready"}:
                continue
            dependencies = [
                by_id.get(dependency) for dependency in task.depends_on
            ]
            if any(
                item is None
                or item.status in {"failed", "blocked", "cancelled"}
                for item in dependencies
            ):
                await self.store.update_task(
                    task.task_id,
                    lambda item: setattr(item, "status", "blocked"),
                )

    async def _finish_run(
        self,
        run_id: str,
        *,
        status: str,
        response: str,
    ) -> RunRecord:
        def finish(run: RunRecord) -> None:
            run.status = status  # type: ignore[assignment]
            run.final_response = response

        return await self.store.update_run(run_id, finish)


async def _resolve(value: Any) -> Any:
    return await value if hasattr(value, "__await__") else value


def _apply_plan(run: RunRecord, plan: Any) -> None:
    run.execution_strategy = plan.execution_strategy
    if not run.success_criteria:
        run.success_criteria = list(plan.success_criteria)


def _completion_status(findings: list[AgentFinding]) -> str:
    if findings and all(item.status == "completed" for item in findings):
        return "completed"
    if any(item.status == "completed" for item in findings):
        return "partial_success"
    return "failed"


def _failed_finding(task: AgentTask, error: str) -> AgentFinding:
    return AgentFinding(
        agent=task.agent_id,
        objective=task.objective,
        status="failed",
        finding=f"专业 Agent 执行失败：{error}",
        confidence=0,
        unresolved_questions=[task.objective],
    )


__all__ = ["RunCoordinator", "SupervisorDecider", "TaskExecutor"]
