"""In-memory Run/Task store with copy-on-read semantics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .run_models import AgentRunRecord, AgentTask, RunRecord, utc_now


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._tasks: dict[str, AgentTask] = {}
        self._agent_runs: dict[str, AgentRunRecord] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, run: RunRecord) -> RunRecord:
        async with self._lock:
            if run.run_id in self._runs:
                raise ValueError(f"run already exists: {run.run_id}")
            self._runs[run.run_id] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    async def add_tasks(
        self,
        run_id: str,
        tasks: list[AgentTask],
    ) -> list[AgentTask]:
        async with self._lock:
            run = self._require_run(run_id)
            duplicates = [
                task.task_id for task in tasks if task.task_id in self._tasks
            ]
            if duplicates:
                raise ValueError(f"tasks already exist: {duplicates}")
            for task in tasks:
                if task.run_id != run_id:
                    raise ValueError(
                        f"task {task.task_id} belongs to {task.run_id}, not {run_id}"
                    )
                self._tasks[task.task_id] = task.model_copy(deep=True)
                run.task_ids.append(task.task_id)
            run.updated_at = utc_now()
        return [item.model_copy(deep=True) for item in tasks]

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            value = self._runs.get(run_id)
            return value.model_copy(deep=True) if value else None

    async def get_task(self, task_id: str) -> AgentTask | None:
        async with self._lock:
            value = self._tasks.get(task_id)
            return value.model_copy(deep=True) if value else None

    async def tasks_for_run(self, run_id: str) -> list[AgentTask]:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return []
            return [
                self._tasks[task_id].model_copy(deep=True)
                for task_id in run.task_ids
            ]

    async def update_run(
        self,
        run_id: str,
        update: Callable[[RunRecord], None],
    ) -> RunRecord:
        async with self._lock:
            run = self._require_run(run_id)
            update(run)
            run.updated_at = utc_now()
            return run.model_copy(deep=True)

    async def update_task(
        self,
        task_id: str,
        update: Callable[[AgentTask], None],
    ) -> AgentTask:
        async with self._lock:
            task = self._require_task(task_id)
            update(task)
            task.updated_at = utc_now()
            run = self._require_run(task.run_id)
            run.updated_at = task.updated_at
            return task.model_copy(deep=True)

    async def add_agent_run(
        self,
        record: AgentRunRecord,
    ) -> AgentRunRecord:
        async with self._lock:
            if record.agent_run_id in self._agent_runs:
                raise ValueError(
                    f"agent run already exists: {record.agent_run_id}"
                )
            self._require_task(record.task_id)
            self._agent_runs[record.agent_run_id] = record.model_copy(deep=True)
            return record.model_copy(deep=True)

    def _require_run(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise LookupError(f"run not found: {run_id}") from exc

    def _require_task(self, task_id: str) -> AgentTask:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise LookupError(f"task not found: {task_id}") from exc


__all__ = ["InMemoryRunStore"]
