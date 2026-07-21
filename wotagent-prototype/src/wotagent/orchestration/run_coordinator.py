"""Run lifecycle coordinator over a Supervisor, Scheduler, and specialists."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ..agents.contracts import AgentFinding, AgentFindingPayload
from .run_models import (
    AgentRunRecord,
    AgentTask,
    DependencyFinding,
    DependencyObservation,
    RunRecord,
    SupervisorDecision,
    TaskFailure,
    TaskPrincipal,
    utc_now,
)
from .run_store import RunStore
from .scheduler import TaskScheduler


SupervisorDecider = Callable[
    [dict[str, Any]],
    SupervisorDecision | Awaitable[SupervisorDecision],
]
TaskExecutor = Callable[
    [AgentTask],
    (
        "AgentFinding | AgentFindingPayload | PendingConfirmation | "
        "Awaitable[AgentFinding | AgentFindingPayload | PendingConfirmation]"
    ),
]


@dataclass
class PendingConfirmation:
    payload: dict[str, Any]


TaskResumer = Callable[
    [AgentTask, bool],
    AgentFinding | Awaitable[AgentFinding],
]
TaskPreparer = Callable[
    [AgentTask, str, dict[str, Any]],
    AgentTask | Awaitable[AgentTask],
]


class TaskPreparationError(ValueError):
    def __init__(self, failure: TaskFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class RunCoordinator:
    def __init__(
        self,
        store: RunStore,
        scheduler: TaskScheduler,
        supervisor: SupervisorDecider,
        executors: dict[str, TaskExecutor],
        resumers: dict[str, TaskResumer] | None = None,
        task_preparer: TaskPreparer | None = None,
        *,
        max_supervisor_iterations: int = 4,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self.supervisor = supervisor
        self.executors = executors
        self.resumers = resumers or {}
        self.task_preparer = task_preparer
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
                    status="needs_more_information",
                    response=decision.clarification,
                )
            if decision.action == "finish":
                completion_status = _completion_status(
                    findings,
                    goal_completed=decision.goal_completed,
                )
                return await self._finish_run(
                    run.run_id,
                    status=completion_status,
                    response=decision.final_response,
                )

            plan = decision.task_plan()
            principal_payload = context.get("principal")
            principal = (
                TaskPrincipal.model_validate(principal_payload)
                if isinstance(principal_payload, dict)
                else TaskPrincipal(
                    principal_id=run.user_id,
                    roles=[run.role],
                )
            )
            tasks = self.scheduler.materialize(
                run.run_id,
                plan,
                principal=principal,
            )
            tasks = await self._prepare_tasks(
                tasks,
                message=message,
                context=context,
            )
            tasks, repeated_failure = await self._reject_repeated_failures(
                run.run_id,
                tasks,
            )
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
                        "principal": (
                            task.principal.model_dump(mode="json")
                            if task.principal else None
                        ),
                        "target": (
                            task.target.model_dump(mode="json")
                            if task.target else None
                        ),
                        "requested_capability": task.requested_capability,
                        "model_requested_capability": (
                            task.model_requested_capability
                        ),
                        "effective_scope": (
                            task.effective_scope.model_dump(mode="json")
                            if task.effective_scope else None
                        ),
                        "failure": (
                            task.failure.model_dump(mode="json")
                            if task.failure else None
                        ),
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
            if repeated_failure:
                return await self._finish_run(
                    run.run_id,
                    status="failed",
                    response=(
                        "检测到相同目标上的重复失败，已停止无效重调度。"
                    ),
                )
            run = await self.store.get_run(run.run_id) or run
            if run.status == "waiting_confirmation":
                return run

        findings = await self._run_findings(run.run_id)
        return await self._finish_run(
            run.run_id,
            status=_completion_status(findings),
            response=(
                "Supervisor 达到最大决策轮次，已返回当前任务结果。"
            ),
        )

    async def _prepare_tasks(
        self,
        tasks: list[AgentTask],
        *,
        message: str,
        context: dict[str, Any],
    ) -> list[AgentTask]:
        for task in tasks:
            task.source_request = message
        if self.task_preparer is None:
            return tasks
        prepared: list[AgentTask] = []
        for task in tasks:
            try:
                prepared.append(
                    await _resolve(self.task_preparer(task, message, context))
                )
            except TaskPreparationError as exc:
                task.status = _failure_task_status(exc.failure)
                task.failure = exc.failure
                task.finding = _failure_finding(
                    task,
                    exc.failure,
                    finding_status=_failure_finding_status(exc.failure),
                )
                prepared.append(task)
        return prepared

    async def _reject_repeated_failures(
        self,
        run_id: str,
        tasks: list[AgentTask],
    ) -> tuple[list[AgentTask], bool]:
        previous = await self.store.tasks_for_run(run_id)
        repeated = False
        for task in tasks:
            if task.failure is not None:
                continue
            matches = [
                item.failure
                for item in previous
                if item.failure is not None
                and _task_subject(item) == _task_subject(task)
            ]
            should_stop = any(not item.retryable for item in matches) or (
                len(matches) >= 2
            )
            if not should_stop:
                continue
            repeated = True
            failure = _make_failure(
                task,
                category="contract",
                code="REPEATED_FAILURE",
                message=(
                    "相同 Agent、能力和目标已经发生不可恢复或超限失败，"
                    "拒绝再次执行。"
                ),
                retryable=False,
            )
            task.status = "failed"
            task.failure = failure
            task.finding = _failure_finding(task, failure)
        return tasks, repeated

    async def confirm(
        self,
        *,
        run_id: str,
        task_id: str,
        approved: bool,
        message: str,
        context: dict[str, Any],
    ) -> RunRecord:
        task = await self.store.get_task(task_id)
        if (
            task is None
            or task.run_id != run_id
            or task.status != "waiting_confirmation"
            or task.confirmation is None
        ):
            raise LookupError("task is not waiting for confirmation")
        agent_runs = await self.store.agent_runs_for_task(task_id)
        waiting_agent_run = next(
            (
                record
                for record in reversed(agent_runs)
                if record.status == "waiting_confirmation"
            ),
            None,
        )
        if waiting_agent_run is None:
            raise LookupError("waiting agent run not found")
        if approved:
            failure: TaskFailure | None = None
            resumer = self.resumers.get(task.agent_id)
            if resumer is None:
                raise LookupError(
                    f"no confirmation resumer registered for {task.agent_id}"
                )
            try:
                finding = _attest_finding(
                    task,
                    waiting_agent_run.agent_run_id,
                    await _resolve(resumer(task, True)),
                )
            except Exception as exc:
                failure = _classify_failure(task, exc)
                finding = _failure_finding(
                    task,
                    failure,
                    waiting_agent_run.agent_run_id,
                )
        else:
            failure = TaskFailure(
                category="authorization",
                code="USER_REJECTED",
                message="用户拒绝了高风险操作，设备未执行。",
                retryable=False,
                fingerprint=_fingerprint(
                    task,
                    "USER_REJECTED",
                ),
            )
            finding = AgentFinding(
                agent=task.agent_id,
                objective=task.objective,
                status="blocked",
                finding="用户拒绝了高风险操作，设备未执行。",
                confidence=1,
                unresolved_questions=task.success_criteria,
                task_id=task.task_id,
                agent_run_id=waiting_agent_run.agent_run_id,
            )
        status = _task_status(finding)
        await self.store.update_task(
            task_id,
            lambda item: _complete_task(
                item,
                finding,
                status,
                failure=failure,
            ),
        )
        await self.store.update_agent_run(
            waiting_agent_run.agent_run_id,
            lambda item: _finish_agent_run(item, status),
        )
        await self.store.record_event(
            run_id,
            "task.confirmation.resolved",
            task_id=task_id,
            agent_run_id=waiting_agent_run.agent_run_id,
            node=task.agent_id,
            data={"approved": approved},
        )
        await self.store.record_event(
            run_id,
            f"task.{status}",
            task_id=task_id,
            agent_run_id=waiting_agent_run.agent_run_id,
            node=task.agent_id,
            data={"finding": finding.model_dump(mode="json")},
        )
        if not approved:
            return await self._finish_run(
                run_id,
                status="failed",
                response="操作已由用户拒绝，未执行高风险设备动作。",
            )
        decisions = [
            event
            for event in await self.store.events(run_id)
            if event.type == "supervisor.decision.completed"
        ]
        next_iteration = len(decisions) + 1
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
        task = await self._attach_dependency_findings(task)
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
            data={
                "objective": task.objective,
                "dependency_finding_count": len(task.dependency_findings),
                "dependency_task_ids": [
                    item.task_id for item in task.dependency_findings
                ],
            },
        )
        executor = self.executors.get(task.agent_id)
        failure: TaskFailure | None = None
        if executor is None:
            failure = _make_failure(
                task,
                category="contract",
                code="EXECUTOR_NOT_REGISTERED",
                message=f"no executor registered for {task.agent_id}",
                retryable=False,
            )
            finding = _failure_finding(
                task,
                failure,
                agent_run.agent_run_id,
            )
        else:
            try:
                outcome = await _resolve(executor(task))
                if isinstance(outcome, PendingConfirmation):
                    await self.store.update_task(
                        task.task_id,
                        lambda item: _wait_for_confirmation(
                            item,
                            outcome.payload,
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
                finding = _attest_finding(
                    task,
                    agent_run.agent_run_id,
                    outcome,
                )
                if finding.status == "failed":
                    failure = _failure_from_finding(task, finding)
            except Exception as exc:
                failure = _classify_failure(task, exc)
                finding = _failure_finding(
                    task,
                    failure,
                    agent_run.agent_run_id,
                )
        status = _task_status(finding)

        await self.store.update_task(
            task.task_id,
            lambda item: _complete_task(
                item,
                finding,
                status,
                failure=failure,
            ),
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

    async def _attach_dependency_findings(
        self,
        task: AgentTask,
    ) -> AgentTask:
        findings: list[DependencyFinding] = []
        observation_budget = 32
        for dependency_id in task.depends_on:
            dependency = await self.store.get_task(dependency_id)
            if dependency is None or dependency.finding is None:
                continue
            dependency_finding = _dependency_finding(
                dependency,
                observation_limit=observation_budget,
            )
            findings.append(dependency_finding)
            observation_budget = max(
                0,
                observation_budget - len(dependency_finding.observations),
            )
        if not findings:
            return task
        return task.model_copy(update={"dependency_findings": findings})

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
                or item.status in {
                    "failed", "blocked", "cancelled", "partial_success",
                    "needs_input", "denied", "unavailable",
                }
                for item in dependencies
            ):
                await self.store.update_task(
                    task.task_id,
                    lambda item: setattr(item, "status", "skipped"),
                )
                await self.store.record_event(
                    task.run_id,
                    "task.skipped",
                    task_id=task.task_id,
                    node="task_scheduler",
                    data={"reason": "dependency did not produce usable evidence"},
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


def _completion_status(
    findings: list[AgentFinding],
    *,
    goal_completed: bool | None = None,
) -> str:
    if goal_completed is True:
        # A chat-only Run may finish without specialist evidence. Once Tasks
        # exist, at least one completed Finding is required before trusting the
        # Supervisor's completion declaration.
        if not findings or any(
            item.status in {"completed", "skipped"} for item in findings
        ):
            return "completed"
        return "failed"
    if goal_completed is False:
        if any(item.status in {"completed", "partial_success"} for item in findings):
            return "partial_success"
        return "failed"
    if findings and all(
        item.status in {"completed", "skipped"} for item in findings
    ):
        return "completed"
    if any(item.status in {"completed", "partial_success"} for item in findings):
        return "partial_success"
    return "failed"


def _attest_finding(
    task: AgentTask,
    agent_run_id: str,
    value: Any,
) -> AgentFinding:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    payload = AgentFindingPayload.model_validate(value)
    return AgentFinding(
        agent=task.agent_id,
        objective=task.objective,
        task_id=task.task_id,
        agent_run_id=agent_run_id,
        **payload.model_dump(),
    )


def _dependency_finding(
    task: AgentTask,
    *,
    observation_limit: int = 16,
) -> DependencyFinding:
    finding = task.finding
    if finding is None:
        raise ValueError(f"dependency task '{task.task_id}' has no finding")
    observations: list[DependencyObservation] = []
    for evidence in finding.evidence:
        remaining = observation_limit - len(observations)
        if remaining <= 0:
            break
        value = evidence.value
        if not evidence.source.endswith(".execution_trace"):
            continue
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="python")
        if not isinstance(value, dict):
            continue
        for item in value.get("steps", [])[:min(16, remaining)]:
            if not isinstance(item, dict):
                continue
            step = item.get("step") if isinstance(item.get("step"), dict) else {}
            execution = (
                item.get("execution")
                if isinstance(item.get("execution"), dict)
                else {}
            )
            observations.append(DependencyObservation(
                source_task_id=task.task_id,
                step_id=str(step.get("id", "")),
                kind=str(step.get("kind", "")),
                device_id=str(step.get("device_id", "")),
                action=str(step.get("action", "")),
                execution_status=str(execution.get("status", "not_executed")),
                result=_bounded_dependency_value(execution.get("raw_result")),
                verification=_bounded_dependency_value(item.get("verification")),
                error=(
                    str(execution["error"])
                    if execution.get("error") is not None
                    else None
                ),
            ))
    return DependencyFinding(
        task_id=task.task_id,
        agent_id=task.agent_id,
        objective=task.objective,
        status=finding.status,
        finding=finding.finding[:1000],
        observations=observations,
        recommended_actions=finding.recommended_actions[:8],
        unresolved_questions=finding.unresolved_questions[:8],
    )


def _bounded_dependency_value(
    value: Any,
    *,
    depth: int = 0,
) -> Any:
    if depth >= 3:
        return str(value)[:200]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _bounded_dependency_value(item, depth=depth + 1)
            for key, item in list(value.items())[:16]
        }
    if isinstance(value, list | tuple):
        return [
            _bounded_dependency_value(item, depth=depth + 1)
            for item in value[:16]
        ]
    if isinstance(value, str):
        return value[:500]
    return value


def _failure_finding(
    task: AgentTask,
    failure: TaskFailure,
    agent_run_id: str | None = None,
    *,
    finding_status: str = "failed",
) -> AgentFinding:
    return AgentFinding(
        agent=task.agent_id,
        objective=task.objective,
        task_id=task.task_id,
        agent_run_id=agent_run_id,
        status=finding_status,
        finding=failure.message,
        confidence=1,
        unresolved_questions=[task.objective],
    )


def _finish_agent_run(record: AgentRunRecord, status: str) -> None:
    record.status = (  # type: ignore[assignment]
        "completed" if status in {"completed", "skipped"} else "failed"
    )
    record.finished_at = utc_now()
    record.iteration_count = 1


def _task_status(finding: AgentFinding) -> str:
    return {
        "completed": "completed",
        "partial_success": "partial_success",
        "skipped": "skipped",
        "needs_more_information": "needs_input",
        "denied": "denied",
        "unavailable": "unavailable",
        "blocked": "blocked",
        "failed": "failed",
    }[finding.status]


def _failure_task_status(failure: TaskFailure) -> str:
    if failure.category == "authorization":
        return "denied"
    if failure.category == "resource_scope":
        return "unavailable"
    if failure.category == "entity_resolution" and failure.retryable:
        return "needs_input"
    return "failed"


def _failure_finding_status(failure: TaskFailure) -> str:
    return {
        "denied": "denied",
        "unavailable": "unavailable",
        "needs_input": "needs_more_information",
    }.get(_failure_task_status(failure), "failed")


def _complete_task(
    task: AgentTask,
    finding: AgentFinding,
    status: str,
    *,
    failure: TaskFailure | None = None,
) -> None:
    task.status = status  # type: ignore[assignment]
    task.finding = finding
    task.failure = failure
    task.confirmation = None


def _wait_for_confirmation(
    task: AgentTask,
    payload: dict[str, Any],
) -> None:
    task.status = "waiting_confirmation"
    task.confirmation = payload


def _classify_failure(task: AgentTask, exc: Exception) -> TaskFailure:
    message = str(exc)
    class_name = exc.__class__.__name__
    if class_name == "ModelCallLimitExceededError":
        return _make_failure(
            task,
            category="budget",
            code="MODEL_CALL_LIMIT_EXCEEDED",
            message=message,
            retryable=False,
        )
    if class_name == "ToolCallLimitExceededError":
        return _make_failure(
            task,
            category="budget",
            code="TOOL_CALL_LIMIT_EXCEEDED",
            message=message,
            retryable=False,
        )
    if class_name == "HealthFindingRepairError":
        return _make_failure(
            task,
            category="model_output",
            code="MODEL_OUTPUT_REPAIR_FAILED",
            message=message,
            retryable=False,
        )
    if isinstance(exc, ValidationError):
        return _make_failure(
            task,
            category="model_output",
            code="MODEL_OUTPUT_INVALID",
            message=message,
            retryable=True,
        )
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return _make_failure(
            task,
            category="infrastructure",
            code="SPECIALIST_TIMEOUT",
            message=message or "specialist execution timed out",
            retryable=True,
        )
    if "RESOURCE_SCOPE_DENIED" in message:
        return _make_failure(
            task,
            category="resource_scope",
            code="RESOURCE_SCOPE_DENIED",
            message=message,
            retryable=False,
        )
    return _make_failure(
        task,
        category="infrastructure",
        code="SPECIALIST_EXECUTION_ERROR",
        message=message or exc.__class__.__name__,
        retryable=False,
    )


def _failure_from_finding(
    task: AgentTask,
    finding: AgentFinding,
) -> TaskFailure:
    failed_call = next(
        (
            item
            for item in reversed(finding.tool_calls)
            if not item.success and item.error_code
        ),
        None,
    )
    if failed_call is not None:
        code = str(failed_call.error_code)
        category = (
            "resource_scope"
            if code == "RESOURCE_SCOPE_DENIED"
            else ("tool_transient" if failed_call.retryable else "tool_permanent")
        )
        return _make_failure(
            task,
            category=category,
            code=code,
            message=failed_call.error_message or finding.finding,
            retryable=failed_call.retryable,
        )
    return _make_failure(
        task,
        category="infrastructure",
        code="SPECIALIST_REPORTED_FAILURE",
        message=finding.finding,
        retryable=False,
    )


def _make_failure(
    task: AgentTask,
    *,
    category: Any,
    code: str,
    message: str,
    retryable: bool,
) -> TaskFailure:
    return TaskFailure(
        category=category,
        code=code,
        message=message,
        retryable=retryable,
        fingerprint=_fingerprint(task, code),
    )


def _fingerprint(task: AgentTask, code: str) -> str:
    value = f"{_task_subject(task)}|{code}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _task_subject(task: AgentTask) -> str:
    target = (
        task.target.id
        if task.target is not None
        else (task.target_mention or "").strip().lower()
    )
    return "|".join([
        task.agent_id,
        task.requested_capability,
        target,
    ])


__all__ = [
    "PendingConfirmation",
    "RunCoordinator",
    "SupervisorDecider",
    "TaskExecutor",
    "TaskPreparationError",
    "TaskPreparer",
    "TaskResumer",
]
