"""Run/Task stores for tests and durable application execution."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from .run_models import (
    AgentRunRecord,
    AgentTask,
    RunEvent,
    RunRecord,
    TraceArtifact,
    utc_now,
)


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._tasks: dict[str, AgentTask] = {}
        self._agent_runs: dict[str, AgentRunRecord] = {}
        self._events: dict[str, list[RunEvent]] = {}
        self._artifacts: dict[str, TraceArtifact] = {}
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

    async def update_agent_run(
        self,
        agent_run_id: str,
        update: Callable[[AgentRunRecord], None],
    ) -> AgentRunRecord:
        async with self._lock:
            try:
                record = self._agent_runs[agent_run_id]
            except KeyError as exc:
                raise LookupError(
                    f"agent run not found: {agent_run_id}"
                ) from exc
            update(record)
            return record.model_copy(deep=True)

    async def agent_runs_for_task(
        self,
        task_id: str,
    ) -> list[AgentRunRecord]:
        async with self._lock:
            return [
                record.model_copy(deep=True)
                for record in self._agent_runs.values()
                if record.task_id == task_id
            ]

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
        raw_data: object | None = None,
    ) -> RunEvent:
        async with self._lock:
            run = self._require_run(run_id)
            events = self._events[run_id]
            event_id = f"{run_id}:{len(events) + 1}"
            artifact = _trace_artifact(
                run_id=run_id,
                event_id=event_id,
                task_id=task_id,
                kind=event_type,
                payload=raw_data,
            )
            event = RunEvent(
                event_id=event_id,
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
                raw_artifact_id=(artifact.artifact_id if artifact else None),
            )
            events.append(event)
            if artifact is not None:
                self._artifacts[artifact.artifact_id] = artifact
            _apply_event_metrics(run, event)
            run.updated_at = event.timestamp
            condition = self._conditions[run_id]
        async with condition:
            condition.notify_all()
        return event.model_copy(deep=True)

    async def get_trace_artifact(
        self,
        artifact_id: str,
    ) -> TraceArtifact | None:
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
            return artifact.model_copy(deep=True) if artifact else None

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


class SQLiteRunStore:
    """Durable Run store; asyncio conditions remain process-local notifications."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._conditions: dict[str, asyncio.Condition] = {}
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_run
                    ON tasks(run_id, position);
                CREATE TABLE IF NOT EXISTS agent_runs (
                    agent_run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_task
                    ON agent_runs(task_id);
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    task_id TEXT,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_events_task
                    ON run_events(run_id, task_id, sequence);
                CREATE TABLE IF NOT EXISTS trace_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    task_id TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trace_artifacts_event
                    ON trace_artifacts(run_id, event_id);
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @asynccontextmanager
    async def _locked_connection(self):
        async with self._lock:
            connection = self._connect()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    async def create_run(self, run: RunRecord) -> RunRecord:
        async with self._locked_connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO runs(run_id, payload) VALUES (?, ?)",
                    (run.run_id, run.model_dump_json()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"run already exists: {run.run_id}") from exc
            self._conditions.setdefault(run.run_id, asyncio.Condition())
        return run.model_copy(deep=True)

    async def add_tasks(
        self,
        run_id: str,
        tasks: list[AgentTask],
    ) -> list[AgentTask]:
        async with self._locked_connection() as connection:
            run = self._load_run(connection, run_id)
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) AS value FROM tasks WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            position = int(row["value"]) + 1
            try:
                for offset, task in enumerate(tasks):
                    if task.run_id != run_id:
                        raise ValueError(
                            f"task {task.task_id} belongs to {task.run_id}, not {run_id}"
                        )
                    connection.execute(
                        """INSERT INTO tasks(task_id, run_id, position, payload)
                           VALUES (?, ?, ?, ?)""",
                        (
                            task.task_id,
                            run_id,
                            position + offset,
                            task.model_dump_json(),
                        ),
                    )
                    run.task_ids.append(task.task_id)
            except sqlite3.IntegrityError as exc:
                raise ValueError("one or more tasks already exist") from exc
            run.updated_at = utc_now()
            self._save_run(connection, run)
        return [task.model_copy(deep=True) for task in tasks]

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self._locked_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return RunRecord.model_validate_json(row["payload"]) if row else None

    async def get_task(self, task_id: str) -> AgentTask | None:
        async with self._locked_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return AgentTask.model_validate_json(row["payload"]) if row else None

    async def tasks_for_run(self, run_id: str) -> list[AgentTask]:
        async with self._locked_connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM tasks WHERE run_id = ? ORDER BY position",
                (run_id,),
            ).fetchall()
            return [
                AgentTask.model_validate_json(row["payload"])
                for row in rows
            ]

    async def update_run(
        self,
        run_id: str,
        update: Callable[[RunRecord], None],
    ) -> RunRecord:
        async with self._locked_connection() as connection:
            run = self._load_run(connection, run_id)
            update(run)
            run.updated_at = utc_now()
            self._save_run(connection, run)
            return run.model_copy(deep=True)

    async def update_task(
        self,
        task_id: str,
        update: Callable[[AgentTask], None],
    ) -> AgentTask:
        async with self._locked_connection() as connection:
            task = self._load_task(connection, task_id)
            update(task)
            task.updated_at = utc_now()
            connection.execute(
                "UPDATE tasks SET payload = ? WHERE task_id = ?",
                (task.model_dump_json(), task_id),
            )
            run = self._load_run(connection, task.run_id)
            run.updated_at = task.updated_at
            self._save_run(connection, run)
            return task.model_copy(deep=True)

    async def add_agent_run(
        self,
        record: AgentRunRecord,
    ) -> AgentRunRecord:
        async with self._locked_connection() as connection:
            self._load_task(connection, record.task_id)
            try:
                connection.execute(
                    """INSERT INTO agent_runs(agent_run_id, task_id, payload)
                       VALUES (?, ?, ?)""",
                    (
                        record.agent_run_id,
                        record.task_id,
                        record.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"agent run already exists: {record.agent_run_id}"
                ) from exc
            return record.model_copy(deep=True)

    async def update_agent_run(
        self,
        agent_run_id: str,
        update: Callable[[AgentRunRecord], None],
    ) -> AgentRunRecord:
        async with self._locked_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM agent_runs WHERE agent_run_id = ?",
                (agent_run_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"agent run not found: {agent_run_id}")
            record = AgentRunRecord.model_validate_json(row["payload"])
            update(record)
            connection.execute(
                "UPDATE agent_runs SET payload = ? WHERE agent_run_id = ?",
                (record.model_dump_json(), agent_run_id),
            )
            return record.model_copy(deep=True)

    async def agent_runs_for_task(
        self,
        task_id: str,
    ) -> list[AgentRunRecord]:
        async with self._locked_connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM agent_runs WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            return [
                AgentRunRecord.model_validate_json(row["payload"])
                for row in rows
            ]

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
        raw_data: object | None = None,
    ) -> RunEvent:
        async with self._locked_connection() as connection:
            run = self._load_run(connection, run_id)
            row = connection.execute(
                """SELECT COALESCE(MAX(sequence), 0) AS value
                   FROM run_events WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
            sequence = int(row["value"]) + 1
            event_id = f"{run_id}:{sequence}"
            artifact = _trace_artifact(
                run_id=run_id,
                event_id=event_id,
                task_id=task_id,
                kind=event_type,
                payload=raw_data,
            )
            event = RunEvent(
                event_id=event_id,
                sequence=sequence,
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
                raw_artifact_id=(artifact.artifact_id if artifact else None),
            )
            connection.execute(
                """INSERT INTO run_events(run_id, sequence, task_id, payload)
                   VALUES (?, ?, ?, ?)""",
                (run_id, sequence, task_id, event.model_dump_json()),
            )
            if artifact is not None:
                connection.execute(
                    """INSERT INTO trace_artifacts(
                           artifact_id, run_id, event_id, task_id, payload
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        artifact.artifact_id,
                        run_id,
                        event_id,
                        task_id,
                        artifact.model_dump_json(),
                    ),
                )
            _apply_event_metrics(run, event)
            run.updated_at = event.timestamp
            self._save_run(connection, run)
            condition = self._conditions.setdefault(
                run_id,
                asyncio.Condition(),
            )
        async with condition:
            condition.notify_all()
        return event.model_copy(deep=True)

    async def get_trace_artifact(
        self,
        artifact_id: str,
    ) -> TraceArtifact | None:
        async with self._locked_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM trace_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            return (
                TraceArtifact.model_validate_json(row["payload"])
                if row else None
            )

    async def events(
        self,
        run_id: str,
        *,
        after: int = 0,
        task_id: str | None = None,
    ) -> list[RunEvent]:
        async with self._locked_connection() as connection:
            query = (
                "SELECT payload FROM run_events "
                "WHERE run_id = ? AND sequence > ?"
            )
            parameters: list[object] = [run_id, after]
            if task_id is not None:
                query += " AND task_id = ?"
                parameters.append(task_id)
            query += " ORDER BY sequence"
            return [
                RunEvent.model_validate_json(row["payload"])
                for row in connection.execute(query, parameters).fetchall()
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
        condition = self._conditions.setdefault(run_id, asyncio.Condition())
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except TimeoutError:
            return []
        return await self.events(run_id, after=after)

    @staticmethod
    def _save_run(
        connection: sqlite3.Connection,
        run: RunRecord,
    ) -> None:
        connection.execute(
            "UPDATE runs SET payload = ? WHERE run_id = ?",
            (run.model_dump_json(), run.run_id),
        )

    @staticmethod
    def _load_run(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> RunRecord:
        row = connection.execute(
            "SELECT payload FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"run not found: {run_id}")
        return RunRecord.model_validate_json(row["payload"])

    @staticmethod
    def _load_task(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> AgentTask:
        row = connection.execute(
            "SELECT payload FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"task not found: {task_id}")
        return AgentTask.model_validate_json(row["payload"])


RunStore = InMemoryRunStore | SQLiteRunStore


def _trace_artifact(
    *,
    run_id: str,
    event_id: str,
    task_id: str | None,
    kind: str,
    payload: object | None,
) -> TraceArtifact | None:
    if payload is None:
        return None
    return TraceArtifact(
        artifact_id=f"trace_{uuid4().hex[:16]}",
        run_id=run_id,
        event_id=event_id,
        task_id=task_id,
        kind=kind,
        payload=payload,
    )


def _apply_event_metrics(run: RunRecord, event: RunEvent) -> None:
    """Project durable RunEvents into the persisted Run summary."""
    data = event.data
    if event.type == "model.request.completed":
        run.usage.model_calls += 1
        for key in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
        ):
            current = getattr(run.usage, key)
            setattr(run.usage, key, current + int(data.get(key, 0) or 0))
        run.usage.estimated_cost += float(
            data.get("estimated_cost", 0) or 0
        )
        run.usage.currency = str(
            data.get("currency", run.usage.currency)
        )
        run.usage.pricing_available = (
            run.usage.pricing_available
            or bool(data.get("pricing_available", False))
        )
        run.usage.model_latency_ms += float(
            data.get("duration_ms", 0) or 0
        )
        if data.get("ttft_ms") is not None:
            run.usage.ttft_ms_total += float(data["ttft_ms"])
            run.usage.ttft_measurements += 1
    elif event.type in {
        "agent.tool.completed",
        "agent.resource.completed",
    }:
        run.usage.tool_calls += 1
    elif event.type == "supervisor.decision.completed":
        run.supervisor_iterations = max(
            run.supervisor_iterations,
            event.iteration or run.supervisor_iterations + 1,
        )
    elif event.type == "agent.loop.completed":
        run.agent_loops += 1
    elif event.type == "agent.recovery.completed":
        run.recoveries += 1

    if event.type.startswith("run.") and event.type in {
        "run.completed",
        "run.partial_success",
        "run.failed",
        "run.cancelled",
    }:
        run.finished_at = event.timestamp
        run.duration_ms = max(
            0,
            (event.timestamp - run.created_at).total_seconds() * 1000,
        )


__all__ = ["InMemoryRunStore", "RunStore", "SQLiteRunStore"]
