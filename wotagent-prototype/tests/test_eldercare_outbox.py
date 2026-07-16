from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from wotagent.eldercare import (
    EldercareMonitorService,
    EldercareRuntime,
    EldercareStore,
    Resident,
    SensorReading,
)
from wotagent.telemetry import InMemoryTraceStore


class FakeGateway:
    async def query_state(self, device_id):
        return {
            "heartRate": 78,
            "spo2": 97,
            "fallDetected": False,
            "observedAt": "2026-07-16T10:00:00+00:00",
        }


def make_store(tmp_path) -> EldercareStore:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        wristband_device_id="wristband-001",
    ))
    return store


def test_monitor_persists_risk_to_outbox_without_calling_butler(tmp_path) -> None:
    store = make_store(tmp_path)
    service = EldercareMonitorService(store, FakeGateway())

    result = asyncio.run(service.ingest(SensorReading(
        resident_id="elderly-001",
        sensor_type="fall_detected",
        value=True,
    )))

    assert result["events"][0]["event_type"] == "fall_detected"
    assert result["outbox"][0]["status"] == "pending"
    assert store.list_actions() == []


def test_butler_consumer_processes_and_acknowledges_outbox(tmp_path) -> None:
    store = make_store(tmp_path)
    service = EldercareMonitorService(store, FakeGateway())
    asyncio.run(service.ingest(SensorReading(
        resident_id="elderly-001",
        sensor_type="fall_detected",
        value=True,
    )))
    runtime = EldercareRuntime(store, trace_store=InMemoryTraceStore())

    results = asyncio.run(runtime.consume_outbox_once())

    assert results[0]["status"] == "succeeded"
    assert store.list_outbox()[0]["status"] == "processed"
    assert {item["action"] for item in store.list_actions()} == {
        "notify_caregiver",
        "create_care_task",
    }


def test_outbox_failed_delivery_returns_to_pending(tmp_path) -> None:
    store = make_store(tmp_path)
    service = EldercareMonitorService(store, FakeGateway())
    asyncio.run(service.ingest(SensorReading(
        resident_id="elderly-001",
        sensor_type="fall_detected",
        value=True,
    )))
    runtime = EldercareRuntime(store)

    async def fail(_event):
        raise ConnectionError("butler unavailable")

    runtime.process_event = fail
    asyncio.run(runtime.consume_outbox_once())

    item = store.list_outbox()[0]
    assert item["status"] == "pending"
    assert item["attempts"] == 1
    assert "butler unavailable" in item["last_error"]


def test_monitor_writes_queryable_heartbeat(tmp_path) -> None:
    store = make_store(tmp_path)
    service = EldercareMonitorService(store, FakeGateway())

    asyncio.run(service.collect_once())
    status = store.service_status("eldercare-monitor")

    assert status is not None
    assert status["status"] == "online"
    assert status["reported_status"] == "running"


def test_stale_processing_lease_is_reclaimed(tmp_path) -> None:
    store = make_store(tmp_path)
    service = EldercareMonitorService(store, FakeGateway())
    asyncio.run(service.ingest(SensorReading(
        resident_id="elderly-001",
        sensor_type="fall_detected",
        value=True,
    )))
    first = store.claim_events()[0]
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE event_outbox SET locked_at = ? WHERE outbox_id = ?",
            (
                (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                first["outbox_id"],
            ),
        )

    reclaimed = store.claim_events(lease_seconds=30)

    assert reclaimed[0]["outbox_id"] == first["outbox_id"]
    assert reclaimed[0]["attempts"] == 2
