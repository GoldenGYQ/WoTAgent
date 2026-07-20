"""SQLite time-series and operational store for the eldercare demo."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...paths import data_path
from .models import HealthSummary, Resident, RiskEvent, SensorReading


class EldercareStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else data_path("eldercare.db")
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
                    wristband_device_id TEXT,
                    mattress_device_id TEXT,
                    environment_sensor_device_id TEXT,
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
                    run_id TEXT,
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
                CREATE TABLE IF NOT EXISTS event_outbox (
                    outbox_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    topic TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    locked_at TEXT,
                    processed_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_status_available
                    ON event_outbox(status, available_at, created_at);
                CREATE TABLE IF NOT EXISTS service_heartbeats (
                    service_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
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
            for name in (
                "wristband_device_id",
                "mattress_device_id",
                "environment_sensor_device_id",
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE residents ADD COLUMN {name} TEXT")
            risk_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(risk_events)"
                )
            }
            if "run_id" not in risk_columns:
                connection.execute(
                    "ALTER TABLE risk_events ADD COLUMN run_id TEXT"
                )

    def upsert_resident(self, resident: Resident) -> Resident:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO residents(
                     resident_id, name, room, caregiver, environment_device_id,
                     wristband_device_id, mattress_device_id,
                     environment_sensor_device_id, active
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(resident_id) DO UPDATE SET
                     name=excluded.name, room=excluded.room,
                     caregiver=excluded.caregiver,
                     environment_device_id=excluded.environment_device_id,
                     wristband_device_id=excluded.wristband_device_id,
                     mattress_device_id=excluded.mattress_device_id,
                     environment_sensor_device_id=excluded.environment_sensor_device_id,
                     active=excluded.active""",
                (
                    resident.resident_id,
                    resident.name,
                    resident.room,
                    resident.caregiver,
                    resident.environment_device_id,
                    resident.wristband_device_id,
                    resident.mattress_device_id,
                    resident.environment_sensor_device_id,
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
                wristband_device_id="wristband-001",
                mattress_device_id="mattress-001",
                environment_sensor_device_id="environment-sensor-001",
            ),
            Resident(
                resident_id="elderly-002",
                name="李奶奶",
                room="A-102",
                caregiver="赵护理员",
                environment_device_id="ac-003",
                wristband_device_id="wristband-002",
                mattress_device_id="mattress-002",
                environment_sensor_device_id="environment-sensor-002",
            ),
        ]
        existing = {item.resident_id: item for item in self.list_residents()}
        for resident in defaults:
            current = existing.get(resident.resident_id)
            if current is None:
                self.upsert_resident(resident)
            else:
                updates = {
                    field: getattr(current, field) or getattr(resident, field)
                    for field in (
                        "environment_device_id",
                        "wristband_device_id",
                        "mattress_device_id",
                        "environment_sensor_device_id",
                    )
                }
                if any(getattr(current, field) != value for field, value in updates.items()):
                    self.upsert_resident(current.model_copy(update=updates))

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

    def enqueue_event(self, event: RiskEvent, *, topic: str = "health.risk") -> dict[str, Any]:
        outbox_id = f"outbox_{uuid4().hex[:12]}"
        created_at = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO event_outbox(
                     outbox_id, event_id, topic, payload_json, status, attempts,
                     available_at, created_at
                   ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)""",
                (
                    outbox_id,
                    event.event_id,
                    topic,
                    event.model_dump_json(),
                    created_at,
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM event_outbox WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
        return self._outbox(row)

    def claim_events(
        self,
        *,
        limit: int = 10,
        lease_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        current = datetime.now(UTC)
        now = current.isoformat()
        stale_lock = (current - timedelta(seconds=lease_seconds)).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM event_outbox
                   WHERE (status = 'pending' AND available_at <= ?)
                      OR (status = 'processing' AND locked_at <= ?)
                   ORDER BY created_at ASC LIMIT ?""",
                (now, stale_lock, max(1, min(limit, 100))),
            ).fetchall()
            ids = [row["outbox_id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""UPDATE event_outbox
                        SET status = 'processing', locked_at = ?, attempts = attempts + 1
                        WHERE outbox_id IN ({placeholders})""",
                    [now, *ids],
                )
            connection.commit()
            claimed = [
                connection.execute(
                    "SELECT * FROM event_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                for outbox_id in ids
            ]
        return [self._outbox(row) for row in claimed if row is not None]

    def complete_event(self, outbox_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE event_outbox
                   SET status = 'processed', processed_at = ?, locked_at = NULL
                   WHERE outbox_id = ?""",
                (now, outbox_id),
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
                "SELECT attempts FROM event_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            attempts = int(row["attempts"]) if row else max_attempts
            status = "failed" if attempts >= max_attempts else "pending"
            available_at = (
                datetime.now(UTC) + timedelta(seconds=delay_seconds)
            ).isoformat()
            connection.execute(
                """UPDATE event_outbox
                   SET status = ?, available_at = ?, locked_at = NULL, last_error = ?
                   WHERE outbox_id = ?""",
                (status, available_at, error[:1000], outbox_id),
            )

    def list_outbox(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM event_outbox ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._outbox(row) for row in rows]

    def heartbeat(
        self,
        service_id: str,
        *,
        status: str = "running",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated_at = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO service_heartbeats(service_id, status, details_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(service_id) DO UPDATE SET
                     status=excluded.status,
                     details_json=excluded.details_json,
                     updated_at=excluded.updated_at""",
                (
                    service_id,
                    status,
                    json.dumps(details or {}, ensure_ascii=False),
                    updated_at,
                ),
            )
        return {
            "service_id": service_id,
            "status": status,
            "details": details or {},
            "updated_at": updated_at,
        }

    def service_status(self, service_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM service_heartbeats WHERE service_id = ?",
                (service_id,),
            ).fetchone()
        if row is None:
            return None
        updated_at = datetime.fromisoformat(row["updated_at"])
        age_seconds = max(
            0.0,
            (datetime.now(UTC) - updated_at.astimezone(UTC)).total_seconds(),
        )
        return {
            "service_id": row["service_id"],
            "status": "online" if age_seconds <= 10 else "offline",
            "reported_status": row["status"],
            "details": json.loads(row["details_json"]),
            "updated_at": row["updated_at"],
            "age_seconds": round(age_seconds, 3),
        }

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

    def get_risk(self, event_id: str) -> RiskEvent | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM risk_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._risk(row) if row else None

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

    def link_risk_run(self, event_id: str, run_id: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE risk_events SET run_id = ? WHERE event_id = ?",
                (run_id, event_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"risk event '{event_id}' not found")

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

    def trend(
        self,
        resident_id: str,
        *,
        since: datetime,
        sensor_types: list[str] | None = None,
    ) -> dict[str, Any]:
        resident = self.get_resident(resident_id)
        if resident is None:
            raise KeyError(f"resident '{resident_id}' not found")
        selected_types = sensor_types or [
            "heart_rate",
            "blood_pressure",
            "spo2",
            "room_temperature",
            "room_humidity",
        ]
        labels = {
            "heart_rate": ("心率", "bpm"),
            "blood_pressure": ("血压", "mmHg"),
            "spo2": ("血氧", "%"),
            "room_temperature": ("室温", "C"),
            "room_humidity": ("湿度", "%"),
        }
        series = []
        for sensor_type in selected_types:
            points = []
            for item in self.readings_since(resident_id, sensor_type, since):
                systolic, diastolic = _blood_pressure_parts(item.value)
                points.append({
                    "observed_at": item.observed_at.astimezone(UTC).isoformat(),
                    "value": item.value,
                    "display_value": _display_reading_value(item.value),
                    "numeric_value": (
                        float(item.value)
                        if isinstance(item.value, (int, float))
                        else systolic
                    ),
                    "secondary_numeric_value": diastolic,
                    "source": item.source,
                })
            label, default_unit = labels.get(sensor_type, (sensor_type, ""))
            series.append({
                "sensor_type": sensor_type,
                "label": label,
                "unit": default_unit,
                "points": points,
            })
        return {
            "resident": resident.model_dump(mode="json"),
            "since": since.astimezone(UTC).isoformat(),
            "generated_at": datetime.now(UTC).isoformat(),
            "series": series,
        }

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
            wristband_device_id=row["wristband_device_id"],
            mattress_device_id=row["mattress_device_id"],
            environment_sensor_device_id=row["environment_sensor_device_id"],
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
            run_id=row["run_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            context=json.loads(row["context_json"]),
        )

    @staticmethod
    def _outbox(row: sqlite3.Row) -> dict[str, Any]:
        return {
            **dict(row),
            "payload": json.loads(row["payload_json"]),
        }


def _blood_pressure_parts(value: Any) -> tuple[float | None, float | None]:
    if not isinstance(value, str):
        return None, None
    parts = [item.strip() for item in value.split("/", 1)]
    if len(parts) != 2 or not all(item.isdigit() for item in parts):
        return None, None
    return float(parts[0]), float(parts[1])


def _display_reading_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)
