"""Run lifecycle coordinator over a Supervisor, Scheduler, and specialists."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..agents.contracts import AgentFinding
from .run_models import (
    AgentRunRecord,
    AgentTask,
    RunRecord,
    SupervisorDecision,
    utc_now,
)
from .run_store import InMemoryRunStore
from .scheduler import TaskScheduler


SupervisorDecider = Callable[
    [dict[str, Any]],
    SupervisorDecision | Awaitable[SupervisorDecision],
]
TaskExecutor = Callable[
    [AgentTask],
    "AgentFinding | PendingConfirmation | Awaitable[AgentFinding | PendingConfirmation]",
]


@dataclass
class PendingConfirmation:
    payload: dict[str, Any]
    resume: Callable[[bool], Awaitable[AgentFinding]]


@dataclass
class _PendingTask:
    confirmation: PendingConfirmation
    agent_run_id: str


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
        self._pending: dict[str, _PendingTask] = {}
        self._continuations: dict[str, tuple[str, dict[str, Any], int]] = {}

    async def execute(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        message: str,
        context: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        resolved_run_id = run_id or self.run_id_factory()
        run = await self.store.get_run(resolved_run_id)
        if run is None:
            run = await self.store.create_run(RunRecord(
                run_id=resolved_run_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                root_goal=message,
                status="running",
            ))
        else:
            if run.conversation_id != conversation_id or run.turn_id != turn_id:
                raise ValueError("existing run identity does not match request")
            run = await self.store.update_run(
                resolved_run_id,
                lambda item: setattr(item, "status", "running"),
            )
        await self.store.record_event(
            run.run_id,
            "run.started",
            node="run_coordinator",
            data={"root_goal": message},
        )
        return await self._decision_loop(
            run,
            message=message,
            context=context or {},
            start_iteration=1,
        )

    async def _decision_loop(
        self,
        run: RunRecord,
        *,
        message: str,
        context: dict[str, Any],
        start_iteration: int,
    ) -> RunRecord:
        for supervisor_iteration in range(
            start_iteration,
            self.max_supervisor_iterations + 1,
        ):
            findings = await self._run_findings(run.run_id)
            decision = await _resolve(self.supervisor({
                "run": run.model_dump(mode="json"),
                "message": message,
                "context": context,
                "findings": [
                    finding.model_dump(mode="json") for finding in findings
                ],
                "supervisor_iteration": supervisor_iteration,
            }))
            decision = SupervisorDecision.model_validate(decision)
            await self.store.record_event(
                run.run_id,
                "supervisor.decision.completed",
                node="butler_run_supervisor",
                iteration=supervisor_iteration,
                data=decision.model_dump(mode="json"),
            )
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
            for task in tasks:
                await self.store.record_event(
                    run.run_id,
                    "task.created",
                    task_id=task.task_id,
                    parent_task_id=task.parent_task_id,
                    node="task_scheduler",
                    data={
                        "agent_id": task.agent_id,
                        "objective": task.objective,
                        "depends_on": task.depends_on,
                        "resource_claims": [
                            item.model_dump(mode="json")
                            for item in task.resource_claims
                        ],
                    },
                )
            await self.store.update_run(
                run.run_id,
                lambda item: _apply_plan(item, plan),
            )
            await self._execute_wave(
                run.run_id,
                plan.execution_strategy,
            )
            run = await self.store.get_run(run.run_id) or run
            if run.status == "waiting_confirmation":
                self._continuations[run.run_id] = (
                    message,
                    context,
                    supervisor_iteration + 1,
                )
                return run

        findings = await self._run_findings(run.run_id)
        return await self._finish_run(
            run.run_id,
            status=_completion_status(findings),
            response=(
                "Supervisor 达到最大决策轮次，已返回当前任务结果。"
            ),
        )

    async def confirm(
        self,
        *,
        run_id: str,
        task_id: str,
        approved: bool,
    ) -> RunRecord:
        pending = self._pending.pop(task_id, None)
        task = await self.store.get_task(task_id)
        if pending is None or task is None or task.run_id != run_id:
            raise LookupError("task is not waiting for confirmation")
        if approved:
            try:
                finding = await pending.confirmation.resume(True)
            except Exception as exc:
                finding = _failed_finding(task, str(exc))
        else:
            finding = AgentFinding(
                agent=task.agent_id,
                objective=task.objective,
                status="blocked",
                finding="用户拒绝了高风险操作，设备未执行。",
                confidence=1,
                unresolved_questions=task.success_criteria,
            )
        status = _task_status(finding)
        await self.store.update_task(
            task_id,
            lambda item: _complete_task(item, finding, status),
        )
        await self.store.update_agent_run(
            pending.agent_run_id,
            lambda item: _finish_agent_run(item, status),
        )
        await self.store.record_event(
            run_id,
            "task.confirmation.resolved",
            task_id=task_id,
            agent_run_id=pending.agent_run_id,
            node=task.agent_id,
            data={"approved": approved},
        )
        await self.store.record_event(
            run_id,
            f"task.{status}",
            task_id=task_id,
            agent_run_id=pending.agent_run_id,
            node=task.agent_id,
            data={"finding": finding.model_dump(mode="json")},
        )
        if not approved:
            return await self._finish_run(
                run_id,
                status="failed",
                response="操作已由用户拒绝，未执行高风险设备动作。",
            )
        continuation = self._continuations.pop(run_id, None)
        if continuation is None:
            raise LookupError("run continuation not found")
        message, context, next_iteration = continuation
        run = await self.store.update_run(
            run_id,
            lambda item: setattr(item, "status", "running"),
        )
        return await self._decision_loop(
            run,
            message=message,
            context=context,
            start_iteration=next_iteration,
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
            await self.store.record_event(
                run_id,
                "scheduler.parallel_batch.completed",
                node="task_scheduler",
                data={
                    "task_ids": [item.task_id for item in batch],
                    "parallel": len(batch) > 1,
                },
            )
            findings.extend(
                result for result in results if result is not None
            )

    async def _execute_task(self, task: AgentTask) -> AgentFinding | None:
        agent_run = await self.store.add_agent_run(AgentRunRecord(
            agent_run_id=f"agent_run_{uuid4().hex[:12]}",
            run_id=task.run_id,
            task_id=task.task_id,
            agent_id=task.agent_id,
        ))
        await self.store.update_task(
            task.task_id,
            lambda item: setattr(item, "status", "running"),
        )
        await self.store.record_event(
            task.run_id,
            "task.started",
            task_id=task.task_id,
            parent_task_id=task.parent_task_id,
            agent_run_id=agent_run.agent_run_id,
            node=task.agent_id,
            data={"objective": task.objective},
        )
        executor = self.executors.get(task.agent_id)
        if executor is None:
            finding = _failed_finding(
                task,
                f"no executor registered for {task.agent_id}",
            )
        else:
            try:
                outcome = await _resolve(executor(task))
                if isinstance(outcome, PendingConfirmation):
                    self._pending[task.task_id] = _PendingTask(
                        confirmation=outcome,
                        agent_run_id=agent_run.agent_run_id,
                    )
                    await self.store.update_task(
                        task.task_id,
                        lambda item: setattr(
                            item,
                            "status",
                            "waiting_confirmation",
                        ),
                    )
                    await self.store.update_agent_run(
                        agent_run.agent_run_id,
                        lambda item: setattr(
                            item,
                            "status",
                            "waiting_confirmation",
                        ),
                    )
                    await self.store.update_run(
                        task.run_id,
                        lambda item: setattr(
                            item,
                            "status",
                            "waiting_confirmation",
                        ),
                    )
                    await self.store.record_event(
                        task.run_id,
                        "task.waiting_confirmation",
                        task_id=task.task_id,
                        agent_run_id=agent_run.agent_run_id,
                        node=task.agent_id,
                        data={"confirmation": outcome.payload},
                    )
                    return None
                finding = AgentFinding.model_validate(outcome)
            except Exception as exc:
                finding = _failed_finding(task, str(exc))
        status = _task_status(finding)

        await self.store.update_task(
            task.task_id,
            lambda item: _complete_task(item, finding, status),
        )
        await self.store.update_agent_run(
            agent_run.agent_run_id,
            lambda item: _finish_agent_run(item, status),
        )
        await self.store.record_event(
            task.run_id,
            f"task.{status}",
            task_id=task.task_id,
            parent_task_id=task.parent_task_id,
            agent_run_id=agent_run.agent_run_id,
            node=task.agent_id,
            data={"finding": finding.model_dump(mode="json")},
        )
        return finding

    async def _run_findings(self, run_id: str) -> list[AgentFinding]:
        return [
            task.finding
            for task in await self.store.tasks_for_run(run_id)
            if task.finding is not None
        ]

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
                await self.store.record_event(
                    task.run_id,
                    "task.blocked",
                    task_id=task.task_id,
                    node="task_scheduler",
                    data={"reason": "dependency failed or missing"},
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

        run = await self.store.update_run(run_id, finish)
        await self.store.record_event(
            run_id,
            f"run.{status}",
            node="run_coordinator",
            data={"response": response},
        )
        return run


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


def _finish_agent_run(record: AgentRunRecord, status: str) -> None:
    record.status = (  # type: ignore[assignment]
        "completed" if status == "completed" else "failed"
    )
    record.finished_at = utc_now()
    record.iteration_count = 1


def _task_status(finding: AgentFinding) -> str:
    return {
        "completed": "completed",
        "needs_more_information": "blocked",
        "blocked": "blocked",
        "failed": "failed",
    }[finding.status]


def _complete_task(
    task: AgentTask,
    finding: AgentFinding,
    status: str,
) -> None:
    task.status = status  # type: ignore[assignment]
    task.finding = finding


__all__ = [
    "PendingConfirmation",
    "RunCoordinator",
    "SupervisorDecider",
    "TaskExecutor",
]
