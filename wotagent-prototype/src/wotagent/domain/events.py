"""Persistent, domain-neutral events submitted to the Agent runtime."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainEventEnvelope(BaseModel):
    event_id: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    message: str = Field(min_length=1)
    source: str = Field(min_length=1)
    status: str = "open"
    occurred_at: datetime = Field(default_factory=utc_now)
    run_goal: str = Field(min_length=1)
    source_context: dict[str, object] = Field(default_factory=dict)
    principal_id: str = Field(min_length=1)
    role: str = "operator"
    run_id: str | None = None

    def public_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "subject_id": self.subject_id,
            "event_type": self.event_type,
            "severity": self.severity,
            "message": self.message,
            "status": self.status,
            "occurred_at": self.occurred_at.isoformat(),
            "source": self.source,
            "run_id": self.run_id,
        }


class ClaimedDomainEvent(BaseModel):
    outbox_id: str = Field(min_length=1)
    event: DomainEventEnvelope


class DomainEventSource(Protocol):
    def claim_events(self, *, limit: int = 10) -> list[ClaimedDomainEvent]: ...

    def complete_event(self, outbox_id: str) -> None: ...

    def retry_event(
        self,
        outbox_id: str,
        error: str,
        *,
        delay_seconds: int = 5,
    ) -> None: ...

    def link_run(self, event_id: str, run_id: str) -> None: ...


class DomainEventStore(DomainEventSource, Protocol):
    def publish(self, event: DomainEventEnvelope) -> DomainEventEnvelope: ...

    def list_events(
        self,
        *,
        domain_id: str,
        subject_id: str,
        severity: str | None = None,
        status: str | None = None,
    ) -> list[DomainEventEnvelope]: ...

    def get_event(self, event_id: str) -> DomainEventEnvelope | None: ...

    def update_status(
        self,
        event_id: str,
        status: str,
    ) -> DomainEventEnvelope: ...


class InMemoryDomainEventStore:
    def __init__(self) -> None:
        self._events: dict[str, DomainEventEnvelope] = {}
        self._outbox: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def publish(self, event: DomainEventEnvelope) -> DomainEventEnvelope:
        with self._lock:
            existing = self._events.get(event.event_id)
            if existing is not None:
                return existing.model_copy(deep=True)
            stored = event.model_copy(deep=True)
            self._events[event.event_id] = stored
            outbox_id = f"outbox_{uuid4().hex[:12]}"
            self._outbox[outbox_id] = {
                "event_id": event.event_id,
                "status": "pending",
                "attempts": 0,
                "available_at": utc_now(),
                "locked_at": None,
                "last_error": "",
            }
            return stored.model_copy(deep=True)

    def list_events(
        self,
        *,
        domain_id: str,
        subject_id: str,
        severity: str | None = None,
        status: str | None = None,
    ) -> list[DomainEventEnvelope]:
        with self._lock:
            items = [
                event.model_copy(deep=True)
                for event in self._events.values()
                if event.domain_id == domain_id
                and event.subject_id == subject_id
                and (severity is None or event.severity == severity)
                and (status is None or event.status == status)
            ]
        return sorted(items, key=lambda item: item.occurred_at, reverse=True)

    def get_event(self, event_id: str) -> DomainEventEnvelope | None:
        with self._lock:
            event = self._events.get(event_id)
            return event.model_copy(deep=True) if event else None

    def update_status(
        self,
        event_id: str,
        status: str,
    ) -> DomainEventEnvelope:
        with self._lock:
            event = self._events.get(event_id)
            if event is None:
                raise KeyError("domain event not found")
            event.status = status
            return event.model_copy(deep=True)

    def claim_events(self, *, limit: int = 10) -> list[ClaimedDomainEvent]:
        now = utc_now()
        claimed: list[ClaimedDomainEvent] = []
        with self._lock:
            for outbox_id, item in self._outbox.items():
                if len(claimed) >= limit:
                    break
                if item["status"] != "pending" or item["available_at"] > now:
                    continue
                item["status"] = "processing"
                item["attempts"] += 1
                item["locked_at"] = now
                claimed.append(ClaimedDomainEvent(
                    outbox_id=outbox_id,
                    event=self._events[item["event_id"]].model_copy(deep=True),
                ))
        return claimed

    def complete_event(self, outbox_id: str) -> None:
        with self._lock:
            self._outbox[outbox_id]["status"] = "processed"
            self._outbox[outbox_id]["locked_at"] = None

    def retry_event(
        self,
        outbox_id: str,
        error: str,
        *,
        delay_seconds: int = 5,
    ) -> None:
        with self._lock:
            item = self._outbox[outbox_id]
            item["status"] = (
                "failed" if item["attempts"] >= 5 else "pending"
            )
            item["available_at"] = utc_now() + timedelta(
                seconds=delay_seconds
            )
            item["locked_at"] = None
            item["last_error"] = error[:1000]

    def link_run(self, event_id: str, run_id: str) -> None:
        with self._lock:
            event = self._events.get(event_id)
            if event is None:
                raise KeyError("domain event not found")
            event.run_id = run_id


class SQLiteDomainEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS domain_events (
                    event_id TEXT PRIMARY KEY,
                    domain_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    run_goal TEXT NOT NULL,
                    source_context_json TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    run_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_domain_events_subject_time
                    ON domain_events(domain_id, subject_id, occurred_at DESC);
                CREATE TABLE IF NOT EXISTS domain_event_outbox (
                    outbox_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    locked_at TEXT,
                    processed_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES domain_events(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_domain_outbox_available
                    ON domain_event_outbox(status, available_at, created_at);
                """
            )

    def publish(self, event: DomainEventEnvelope) -> DomainEventEnvelope:
        now = utc_now().isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO domain_events (
                    event_id, domain_id, subject_id, event_type, severity,
                    message, source, status, occurred_at, run_goal,
                    source_context_json, principal_id, role, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.domain_id,
                    event.subject_id,
                    event.event_type,
                    event.severity,
                    event.message,
                    event.source,
                    event.status,
                    event.occurred_at.astimezone(UTC).isoformat(),
                    event.run_goal,
                    json.dumps(event.source_context, ensure_ascii=False),
                    event.principal_id,
                    event.role,
                    event.run_id,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO domain_event_outbox (
                    outbox_id, event_id, status, attempts, available_at,
                    created_at
                ) VALUES (?, ?, 'pending', 0, ?, ?)
                """,
                (f"outbox_{uuid4().hex[:12]}", event.event_id, now, now),
            )
            connection.commit()
        stored = self.get_event(event.event_id)
        if stored is None:
            raise RuntimeError("published domain event was not persisted")
        return stored

    def list_events(
        self,
        *,
        domain_id: str,
        subject_id: str,
        severity: str | None = None,
        status: str | None = None,
    ) -> list[DomainEventEnvelope]:
        clauses = ["domain_id = ?", "subject_id = ?"]
        parameters: list[object] = [domain_id, subject_id]
        if severity is not None:
            clauses.append("severity = ?")
            parameters.append(severity)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM domain_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY occurred_at DESC",
                parameters,
            ).fetchall()
        return [self._event(row) for row in rows]

    def get_event(self, event_id: str) -> DomainEventEnvelope | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM domain_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._event(row) if row else None

    def update_status(
        self,
        event_id: str,
        status: str,
    ) -> DomainEventEnvelope:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE domain_events SET status = ? WHERE event_id = ?",
                (status, event_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("domain event not found")
        event = self.get_event(event_id)
        if event is None:
            raise KeyError("domain event not found")
        return event

    def claim_events(
        self,
        *,
        limit: int = 10,
        lease_seconds: int = 30,
    ) -> list[ClaimedDomainEvent]:
        current = utc_now()
        now = current.isoformat()
        stale = (current - timedelta(seconds=lease_seconds)).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT outbox_id, event_id FROM domain_event_outbox
                WHERE (status = 'pending' AND available_at <= ?)
                   OR (status = 'processing' AND locked_at <= ?)
                ORDER BY created_at ASC LIMIT ?
                """,
                (now, stale, max(1, min(limit, 100))),
            ).fetchall()
            ids = [row["outbox_id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE domain_event_outbox
                    SET status = 'processing', locked_at = ?,
                        attempts = attempts + 1
                    WHERE outbox_id IN ({placeholders})
                    """,
                    [now, *ids],
                )
            connection.commit()
        claimed: list[ClaimedDomainEvent] = []
        for row in rows:
            event = self.get_event(row["event_id"])
            if event is not None:
                claimed.append(ClaimedDomainEvent(
                    outbox_id=row["outbox_id"],
                    event=event,
                ))
        return claimed

    def complete_event(self, outbox_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE domain_event_outbox
                SET status = 'processed', processed_at = ?, locked_at = NULL
                WHERE outbox_id = ?
                """,
                (utc_now().isoformat(), outbox_id),
            )

    def retry_event(
        self,
        outbox_id: str,
        error: str,
        *,
        delay_seconds: int = 5,
        max_attempts: int = 5,
    ) -> None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT attempts FROM domain_event_outbox
                WHERE outbox_id = ?
                """,
                (outbox_id,),
            ).fetchone()
            attempts = int(row["attempts"]) if row else max_attempts
            status = "failed" if attempts >= max_attempts else "pending"
            available_at = (
                utc_now() + timedelta(seconds=delay_seconds)
            ).isoformat()
            connection.execute(
                """
                UPDATE domain_event_outbox
                SET status = ?, available_at = ?, locked_at = NULL,
                    last_error = ?
                WHERE outbox_id = ?
                """,
                (status, available_at, error[:1000], outbox_id),
            )

    def link_run(self, event_id: str, run_id: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE domain_events SET run_id = ? WHERE event_id = ?",
                (run_id, event_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("domain event not found")

    @staticmethod
    def _event(row: sqlite3.Row) -> DomainEventEnvelope:
        return DomainEventEnvelope(
            event_id=row["event_id"],
            domain_id=row["domain_id"],
            subject_id=row["subject_id"],
            event_type=row["event_type"],
            severity=row["severity"],
            message=row["message"],
            source=row["source"],
            status=row["status"],
            occurred_at=row["occurred_at"],
            run_goal=row["run_goal"],
            source_context=json.loads(row["source_context_json"]),
            principal_id=row["principal_id"],
            role=row["role"],
            run_id=row["run_id"],
        )


__all__ = [
    "ClaimedDomainEvent",
    "DomainEventEnvelope",
    "DomainEventSource",
    "DomainEventStore",
    "InMemoryDomainEventStore",
    "SQLiteDomainEventStore",
]
