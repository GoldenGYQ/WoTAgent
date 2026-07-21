from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from wotagent.domains.eldercare import (
    EldercareRuntime,
    EldercareStore,
    Resident,
    RiskEvent,
)
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


class FakeVillaGateway:
    def __init__(self) -> None:
        self.state = {
            "smokeLevel": 0,
            "alarm": False,
            "observedAt": "2026-07-19T08:00:00+00:00",
        }

    async def list_devices(self):
        return [{
            "id": "smoke-sensor-001",
            "title": "Hall Smoke Sensor",
            "location": "hall",
            "actions": [{"name": "updateSmokeState"}],
        }]

    async def query_state(self, device_id):
        assert device_id == "smoke-sensor-001"
        return dict(self.state)

    async def control_device(
        self,
        device_id,
        action,
        parameters,
        *,
        idempotency_key,
    ):
        assert device_id == "smoke-sensor-001"
        assert action == "updateSmokeState"
        assert idempotency_key.startswith("domain-")
        self.state.update(parameters)
        return {"success": True}


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
    assert {
        item["domain_id"]
        for item in domains.json()["domains"]
    } == {"eldercare", "smart_home"}
    assert [item["subject_id"] for item in subjects.json()["subjects"]] == [
        "elderly-001"
    ]
    assert resources.json()["resources"][0]["resource_id"] == "wristband-001"
    assert denied.status_code == 403
    assert monitor.json()["status"] == "offline"


def test_smart_home_domain_api_enforces_site_relationship_scope(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        app_module._runtime,
        "gateway",
        FakeVillaGateway(),
    )
    monkeypatch.setenv("ELDERCARE_EVENT_CONSUMER_ENABLED", "false")

    with TestClient(app_module.app) as client:
        subjects = client.get(
            "/api/v2/domains/smart_home/subjects",
            params={"principal_id": "JH-1"},
        )
        resources = client.get(
            "/api/v2/domains/smart_home/subjects/villa-demo/resources",
            params={"principal_id": "JH-2"},
        )
        allowed = client.post(
            "/api/v2/domains/smart_home/scenarios",
            json={
                "subject_id": "villa-demo",
                "scenario": "smoke_alarm",
                "principal_id": "JH-1",
            },
        )
        denied = client.post(
            "/api/v2/domains/smart_home/scenarios",
            json={
                "subject_id": "villa-demo",
                "scenario": "smoke_alarm",
                "principal_id": "JH-2",
            },
        )
        events = client.get(
            "/api/v2/domains/smart_home/subjects/villa-demo/events",
            params={"principal_id": "JH-1"},
        )

    assert subjects.status_code == 200
    assert subjects.json()["subjects"][0]["subject_id"] == "villa-demo"
    assert resources.status_code == 200
    assert resources.json()["resources"][0]["resource_id"] == (
        "smoke-sensor-001"
    )
    assert allowed.status_code == 202
    assert allowed.json()["verified"] is True
    assert denied.status_code == 403
    assert events.json()["events"][0]["event_type"] == "smoke_alarm"


def test_eldercare_event_filters_and_status_transitions(
    monkeypatch,
    tmp_path,
) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
    ))
    critical = store.create_risk(RiskEvent(
        resident_id="elderly-001",
        event_type="fall_detected",
        severity="critical",
        value=True,
        message="疑似跌倒",
    ))
    store.create_risk(RiskEvent(
        resident_id="elderly-001",
        event_type="room_humidity_out_of_range",
        severity="medium",
        value=28,
        message="湿度偏低",
    ))
    monkeypatch.setattr(app_module._runtime, "eldercare_store", store)
    monkeypatch.setenv("ELDERCARE_EVENT_CONSUMER_ENABLED", "false")

    with TestClient(app_module.app) as client:
        filtered = client.get(
            "/api/v2/domains/eldercare/subjects/elderly-001/events",
            params={
                "principal_id": "JH-1",
                "severity": "critical",
                "status": "open",
            },
        )
        acknowledged = client.post(
            f"/api/v2/domains/eldercare/events/{critical.event_id}/status",
            json={"principal_id": "JH-1", "status": "acknowledged"},
        )
        in_progress = client.post(
            f"/api/v2/domains/eldercare/events/{critical.event_id}/status",
            json={"principal_id": "JH-1", "status": "in_progress"},
        )
        resolved = client.post(
            f"/api/v2/domains/eldercare/events/{critical.event_id}/status",
            json={"principal_id": "JH-1", "status": "resolved"},
        )
        invalid = client.post(
            f"/api/v2/domains/eldercare/events/{critical.event_id}/status",
            json={"principal_id": "JH-1", "status": "in_progress"},
        )

    assert [item["event_id"] for item in filtered.json()["events"]] == [
        critical.event_id
    ]
    assert acknowledged.json()["status"] == "acknowledged"
    assert in_progress.json()["status"] == "in_progress"
    assert resolved.json()["status"] == "resolved"
    assert invalid.status_code == 422
