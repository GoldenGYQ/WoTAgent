"""In-memory Run/Task store with copy-on-read semantics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .run_models import (
    AgentRunRecord,
    AgentTask,
    RunEvent,
    RunRecord,
    utc_now,
)


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._tasks: dict[str, AgentTask] = {}
        self._agent_runs: dict[str, AgentRunRecord] = {}
        self._events: dict[str, list[RunEvent]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, run: RunRecord) -> RunRecord:
        async with self._lock:
            if run.run_id in self._runs:
                raise ValueError(f"run already exists: {run.run_id}")
            self._runs[run.run_id] = run.model_copy(deep=True)
            self._events[run.run_id] = []
            self._conditions[run.run_id] = asyncio.Condition()
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

    async def record_event(
        self,
        run_id: str,
        event_type: str,
        *,
        task_id: str | None = None,
        parent_task_id: str | None = None,
        agent_run_id: str | None = None,
        iteration: int = 0,
        tool_call_id: str | None = None,
        node: str = "",
        data: dict[str, object] | None = None,
    ) -> RunEvent:
        async with self._lock:
            run = self._require_run(run_id)
            events = self._events[run_id]
            event = RunEvent(
                event_id=f"{run_id}:{len(events) + 1}",
                sequence=len(events) + 1,
                run_id=run_id,
                turn_id=run.turn_id,
                task_id=task_id,
                parent_task_id=parent_task_id,
                agent_run_id=agent_run_id,
                iteration=iteration,
                tool_call_id=tool_call_id,
                type=event_type,
                node=node,
                data=data or {},
            )
            events.append(event)
            run.updated_at = event.timestamp
            condition = self._conditions[run_id]
        async with condition:
            condition.notify_all()
        return event.model_copy(deep=True)

    async def events(
        self,
        run_id: str,
        *,
        after: int = 0,
        task_id: str | None = None,
    ) -> list[RunEvent]:
        async with self._lock:
            return [
                event.model_copy(deep=True)
                for event in self._events.get(run_id, [])
                if event.sequence > after
                and (task_id is None or event.task_id == task_id)
            ]

    async def wait_for_events(
        self,
        run_id: str,
        *,
        after: int,
        timeout: float = 15,
    ) -> list[RunEvent]:
        current = await self.events(run_id, after=after)
        if current:
            return current
        condition = self._conditions.get(run_id)
        if condition is None:
            return []
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except TimeoutError:
            return []
        return await self.events(run_id, after=after)

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
