from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from wotagent.domains.eldercare import EldercareRuntime, EldercareStore, Resident
from wotagent.telemetry import InMemoryTraceStore

app_module = importlib.import_module("wotagent.api.app")


class FakeGateway:
    async def list_devices(self):
        return [{
            "id": "wristband-001",
            "title": "健康手环",
            "actions": [{"name": "updateVitals"}],
        }]

    async def query_state(self, device_id):
        assert device_id == "wristband-001"
        return {
            "heartRate": 82,
            "spo2": 97,
            "observedAt": "2026-07-18T08:00:00+00:00",
        }


def test_domain_api_projects_eldercare_and_filters_demo_subject_scope(
    monkeypatch,
    tmp_path,
) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        wristband_device_id="wristband-001",
    ))
    store.upsert_resident(Resident(
        resident_id="elderly-002",
        name="李奶奶",
        room="A-102",
    ))
    gateway = FakeGateway()
    runtime = EldercareRuntime(
        store,
        trace_store=InMemoryTraceStore(),
        environment_gateway=gateway,
    )
    monkeypatch.setattr(app_module._runtime, "eldercare_store", store)
    monkeypatch.setattr(app_module._runtime, "eldercare_runtime", runtime)
    monkeypatch.setattr(app_module._runtime, "gateway", gateway)
    monkeypatch.setenv("ELDERCARE_EVENT_CONSUMER_ENABLED", "false")

    with TestClient(app_module.app) as client:
        domains = client.get("/api/v2/domains")
        subjects = client.get(
            "/api/v2/domains/eldercare/subjects",
            params={"principal_id": "JH-1"},
        )
        resources = client.get(
            "/api/v2/domains/eldercare/subjects/elderly-001/resources",
            params={"principal_id": "JH-1"},
        )
        denied = client.get(
            "/api/v2/domains/eldercare/subjects/elderly-001/overview",
            params={"principal_id": "JH-2"},
        )
        monitor = client.get(
            "/api/v2/domains/eldercare/monitor/status"
        )

    assert domains.status_code == 200
    assert domains.json()["domains"][0]["domain_id"] == "eldercare"
    assert [item["subject_id"] for item in subjects.json()["subjects"]] == [
        "elderly-001"
    ]
    assert resources.json()["resources"][0]["resource_id"] == "wristband-001"
    assert denied.status_code == 403
    assert monitor.json()["status"] == "offline"
