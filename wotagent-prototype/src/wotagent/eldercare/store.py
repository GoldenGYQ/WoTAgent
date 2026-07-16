"""SQLite time-series and operational store for the eldercare demo."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import HealthSummary, Resident, RiskEvent, SensorReading


class EldercareStore:
    def __init__(self, path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[3]
        self.path = Path(path) if path is not None else root / "data" / "eldercare.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS residents (
                    resident_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    room TEXT NOT NULL,
                    caregiver TEXT NOT NULL,
                    environment_device_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    reading_id TEXT PRIMARY KEY,
                    resident_id TEXT NOT NULL,
                    sensor_type TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    source TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_readings_resident_type_time
                    ON sensor_readings(resident_id, sensor_type, observed_at DESC);
                CREATE TABLE IF NOT EXISTS risk_events (
                    event_id TEXT PRIMARY KEY,
                    resident_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_risks_resident_time
                    ON risk_events(resident_id, occurred_at DESC);
                CREATE TABLE IF NOT EXISTS care_actions (
                    action_id TEXT PRIMARY KEY,
                    resident_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(residents)")
            }
            if "environment_device_id" not in columns:
                connection.execute(
                    "ALTER TABLE residents ADD COLUMN environment_device_id TEXT"
                )

    def upsert_resident(self, resident: Resident) -> Resident:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO residents(
                     resident_id, name, room, caregiver, environment_device_id, active
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(resident_id) DO UPDATE SET
                     name=excluded.name, room=excluded.room,
                     caregiver=excluded.caregiver,
                     environment_device_id=excluded.environment_device_id,
                     active=excluded.active""",
                (
                    resident.resident_id,
                    resident.name,
                    resident.room,
                    resident.caregiver,
                    resident.environment_device_id,
                    int(resident.active),
                ),
            )
        return resident

    def seed_demo(self) -> None:
        defaults = [
            Resident(
                resident_id="elderly-001",
                name="张爷爷",
                room="A-101",
                caregiver="王护理员",
                environment_device_id="ac-002",
            ),
            Resident(
                resident_id="elderly-002",
                name="李奶奶",
                room="A-102",
                caregiver="赵护理员",
                environment_device_id="ac-003",
            ),
        ]
        existing = {item.resident_id: item for item in self.list_residents()}
        for resident in defaults:
            current = existing.get(resident.resident_id)
            if current is None:
                self.upsert_resident(resident)
            elif current.environment_device_id is None:
                self.upsert_resident(current.model_copy(update={
                    "environment_device_id": resident.environment_device_id,
                }))

    def list_residents(self) -> list[Resident]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM residents WHERE active = 1 ORDER BY room, resident_id"
            ).fetchall()
        return [self._resident(row) for row in rows]

    def get_resident(self, resident_id: str) -> Resident | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM residents WHERE resident_id = ?", (resident_id,)
            ).fetchone()
        return self._resident(row) if row else None

    def add_reading(self, reading: SensorReading) -> SensorReading:
        if self.get_resident(reading.resident_id) is None:
            raise KeyError(f"resident '{reading.resident_id}' not found")
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO sensor_readings
                   (reading_id, resident_id, sensor_type, value_json, unit, source,
                    observed_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    reading.reading_id,
                    reading.resident_id,
                    reading.sensor_type,
                    json.dumps(reading.value, ensure_ascii=False),
                    reading.unit,
                    reading.source,
                    reading.observed_at.astimezone(UTC).isoformat(),
                    json.dumps(reading.metadata, ensure_ascii=False),
                ),
            )
        return reading

    def readings_since(
        self,
        resident_id: str,
        sensor_type: str,
        since: datetime,
    ) -> list[SensorReading]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM sensor_readings
                   WHERE resident_id = ? AND sensor_type = ? AND observed_at >= ?
                   ORDER BY observed_at ASC""",
                (resident_id, sensor_type, since.astimezone(UTC).isoformat()),
            ).fetchall()
        return [self._reading(row) for row in rows]

    def latest(self, resident_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT r.* FROM sensor_readings r
                   JOIN (
                     SELECT sensor_type, MAX(observed_at) AS max_time
                     FROM sensor_readings WHERE resident_id = ? GROUP BY sensor_type
                   ) latest
                   ON r.sensor_type = latest.sensor_type AND r.observed_at = latest.max_time
                   WHERE r.resident_id = ?""",
                (resident_id, resident_id),
            ).fetchall()
        return {
            row["sensor_type"]: {
                "value": json.loads(row["value_json"]),
                "unit": row["unit"],
                "observed_at": row["observed_at"],
                "source": row["source"],
            }
            for row in rows
        }

    def create_risk(self, event: RiskEvent, *, cooldown_minutes: int = 10) -> RiskEvent | None:
        cutoff = event.occurred_at - timedelta(minutes=cooldown_minutes)
        with self._lock, self._connect() as connection:
            duplicate = connection.execute(
                """SELECT 1 FROM risk_events
                   WHERE resident_id = ? AND event_type = ? AND occurred_at >= ?
                   LIMIT 1""",
                (event.resident_id, event.event_type, cutoff.astimezone(UTC).isoformat()),
            ).fetchone()
            if duplicate:
                return None
            connection.execute(
                """INSERT INTO risk_events
                   (event_id, resident_id, event_type, severity, value_json, message,
                    source, status, occurred_at, context_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.resident_id,
                    event.event_type,
                    event.severity,
                    json.dumps(event.value),
                    event.message,
                    event.source,
                    event.status,
                    event.occurred_at.astimezone(UTC).isoformat(),
                    json.dumps(event.context, ensure_ascii=False),
                ),
            )
        return event

    def list_risks(self, resident_id: str | None = None, *, limit: int = 100) -> list[RiskEvent]:
        query = "SELECT * FROM risk_events"
        params: list[Any] = []
        if resident_id:
            query += " WHERE resident_id = ?"
            params.append(resident_id)
        query += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._risk(row) for row in rows]

    def handle_risk(self, event_id: str) -> RiskEvent | None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE risk_events SET status = 'handled' WHERE event_id = ?",
                (event_id,),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM risk_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._risk(row) if row else None

    def summary(self, resident_id: str) -> HealthSummary:
        resident = self.get_resident(resident_id)
        if resident is None:
            raise KeyError(f"resident '{resident_id}' not found")
        since = datetime.now(UTC) - timedelta(hours=24)
        heart_rates = self.readings_since(resident_id, "heart_rate", since)
        numeric = [float(item.value) for item in heart_rates if isinstance(item.value, (int, float))]
        return HealthSummary(
            resident=resident,
            latest=self.latest(resident_id),
            heart_rate_avg=round(sum(numeric) / len(numeric), 1) if numeric else None,
            open_risks=[item for item in self.list_risks(resident_id) if item.status == "open"],
        )

    def record_action(
        self,
        *,
        resident_id: str,
        agent_id: str,
        action: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        action_id = f"action_{uuid4().hex[:12]}"
        created_at = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO care_actions
                   (action_id, resident_id, agent_id, action, arguments_json, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    action_id,
                    resident_id,
                    agent_id,
                    action,
                    json.dumps(arguments, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    created_at,
                ),
            )
        return {"action_id": action_id, "created_at": created_at, **result}

    def list_actions(self, resident_id: str | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM care_actions"
        params: list[Any] = []
        if resident_id:
            query += " WHERE resident_id = ?"
            params.append(resident_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                **dict(row),
                "arguments": json.loads(row["arguments_json"]),
                "result": json.loads(row["result_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _resident(row: sqlite3.Row) -> Resident:
        return Resident(
            resident_id=row["resident_id"],
            name=row["name"],
            room=row["room"],
            caregiver=row["caregiver"],
            environment_device_id=row["environment_device_id"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _reading(row: sqlite3.Row) -> SensorReading:
        return SensorReading(
            reading_id=row["reading_id"],
            resident_id=row["resident_id"],
            sensor_type=row["sensor_type"],
            value=json.loads(row["value_json"]),
            unit=row["unit"],
            source=row["source"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _risk(row: sqlite3.Row) -> RiskEvent:
        return RiskEvent(
            event_id=row["event_id"],
            resident_id=row["resident_id"],
            event_type=row["event_type"],
            severity=row["severity"],
            value=json.loads(row["value_json"]),
            message=row["message"],
            source=row["source"],
            status=row["status"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            context=json.loads(row["context_json"]),
        )
