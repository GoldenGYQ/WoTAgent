from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from wotagent.eldercare import EldercareRuntime, EldercareStore, Resident
from wotagent.telemetry import InMemoryTraceStore

app_module = importlib.import_module("wotagent.api.app")


def test_eldercare_api_simulates_event_and_reuses_trace_api(monkeypatch, tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        caregiver="王护理员",
    ))
    traces = InMemoryTraceStore()
    runtime = EldercareRuntime(store, trace_store=traces)
    monkeypatch.setattr(app_module, "_eldercare_store", store)
    monkeypatch.setattr(app_module, "_eldercare_runtime", runtime)
    monkeypatch.setattr(app_module, "_v2_trace_store", traces)
    monkeypatch.setenv("ELDERCARE_MONITOR_ENABLED", "false")

    with TestClient(app_module.app) as client:
        residents = client.get("/api/v2/eldercare/residents")
        simulated = client.post("/api/v2/eldercare/simulate", json={
            "resident_id": "elderly-001",
            "scenario": "fall",
        })
        task_id = simulated.json()["tasks"][0]["task_id"]
        task = client.get(f"/api/v2/tasks/{task_id}")
        events = client.get(f"/api/v2/tasks/{task_id}/events")
        actions = client.get(
            "/api/v2/eldercare/actions",
            params={"resident_id": "elderly-001"},
        )

    assert residents.status_code == 200
    assert residents.json()["residents"][0]["name"] == "张爷爷"
    assert simulated.status_code == 202
    assert task.status_code == 200 and task.json()["status"] == "succeeded"
    assert any(item["type"] == "agent.subgraph.completed" for item in events.json()["events"])
    assert {item["action"] for item in actions.json()["actions"]} == {
        "notify_caregiver",
        "create_care_task",
    }


def test_eldercare_manual_reading_and_summary(monkeypatch, tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
    ))
    runtime = EldercareRuntime(store, trace_store=InMemoryTraceStore())
    monkeypatch.setattr(app_module, "_eldercare_store", store)
    monkeypatch.setattr(app_module, "_eldercare_runtime", runtime)

    client = TestClient(app_module.app)
    ingested = client.post("/api/v2/eldercare/readings", json={
        "resident_id": "elderly-001",
        "sensor_type": "sleep_quality",
        "value": "poor",
        "source": "web-console",
    })
    summary = client.get("/api/v2/eldercare/residents/elderly-001/summary")

    assert ingested.status_code == 202
    assert ingested.json()["tasks"] == []
    assert summary.json()["latest"]["sleep_quality"]["value"] == "poor"
