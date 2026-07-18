"""Deterministic DAG and resource-conflict scheduler."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from uuid import uuid4

from .run_models import AgentTask, SupervisorTaskPlan, TaskPrincipal


TaskIdFactory = Callable[[], str]


class TaskScheduler:
    def __init__(
        self,
        *,
        available_agents: set[str],
        max_tasks_per_run: int = 16,
        max_parallel_tasks: int = 4,
        task_id_factory: TaskIdFactory | None = None,
    ) -> None:
        self.available_agents = available_agents
        self.max_tasks_per_run = max_tasks_per_run
        self.max_parallel_tasks = max_parallel_tasks
        self.task_id_factory = task_id_factory or (
            lambda: f"task_{uuid4().hex[:12]}"
        )

    def materialize(
        self,
        run_id: str,
        plan: SupervisorTaskPlan,
        *,
        parent_task_id: str | None = None,
        principal: TaskPrincipal | None = None,
    ) -> list[AgentTask]:
        if len(plan.tasks) > self.max_tasks_per_run:
            raise ValueError(
                f"plan has {len(plan.tasks)} tasks; limit is "
                f"{self.max_tasks_per_run}"
            )
        invalid_agents = sorted({
            item.agent_id
            for item in plan.tasks
            if item.agent_id not in self.available_agents
        })
        if invalid_agents:
            raise ValueError(f"unavailable agents: {invalid_agents}")

        ids = {item.local_id: self.task_id_factory() for item in plan.tasks}
        if len(set(ids.values())) != len(ids):
            raise ValueError("task_id_factory returned duplicate IDs")
        return [
            AgentTask(
                task_id=ids[item.local_id],
                run_id=run_id,
                parent_task_id=parent_task_id,
                local_id=item.local_id,
                agent_id=item.agent_id,
                objective=item.objective,
                reason=item.reason,
                expected_evidence=item.expected_evidence,
                success_criteria=item.success_criteria,
                depends_on=[ids[value] for value in item.depends_on],
                resource_claims=item.resource_claims,
                parallel_candidate=item.parallel_candidate,
                priority=item.priority,
                principal=principal,
                target_type=item.target_type,
                target_mention=item.target_mention,
                requested_capability=item.requested_capability,
            )
            for item in plan.tasks
        ]

    def ready_tasks(self, tasks: Iterable[AgentTask]) -> list[AgentTask]:
        task_list = list(tasks)
        by_id = {item.task_id: item for item in task_list}
        ready: list[AgentTask] = []
        for task in task_list:
            if task.status not in {"pending", "ready"}:
                continue
            dependencies = [by_id.get(task_id) for task_id in task.depends_on]
            if any(item is None for item in dependencies):
                raise ValueError(
                    f"task '{task.task_id}' references a missing dependency"
                )
            if any(
                item.status in {"failed", "blocked", "cancelled"}
                for item in dependencies
                if item is not None
            ):
                continue
            if all(
                item.status == "completed"
                for item in dependencies
                if item is not None
            ):
                ready.append(task)
        return sorted(ready, key=lambda item: (item.priority, item.created_at))

    def next_batch(
        self,
        tasks: Iterable[AgentTask],
        *,
        execution_strategy: str = "parallel_when_safe",
    ) -> list[AgentTask]:
        ready = self.ready_tasks(tasks)
        if not ready:
            return []
        if execution_strategy == "sequential":
            return ready[:1]

        batch: list[AgentTask] = []
        for candidate in ready:
            if len(batch) >= self.max_parallel_tasks:
                break
            if not candidate.parallel_candidate and batch:
                continue
            if any(_tasks_conflict(candidate, selected) for selected in batch):
                continue
            batch.append(candidate)
            if not candidate.parallel_candidate:
                break
        return batch


def _tasks_conflict(left: AgentTask, right: AgentTask) -> bool:
    right_claims = {item.key: item.mode for item in right.resource_claims}
    for claim in left.resource_claims:
        other_mode = right_claims.get(claim.key)
        if other_mode is None:
            continue
        if claim.mode == "write" or other_mode == "write":
            return True
    return False


__all__ = ["TaskScheduler"]
