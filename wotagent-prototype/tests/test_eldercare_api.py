from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from wotagent.conversation import ConversationStore
from wotagent.eldercare import EldercareRuntime, EldercareStore, Resident, RiskEvent
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


def test_main_chat_task_routes_eldercare_request_and_persists_response(
    monkeypatch,
    tmp_path,
) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
    ))
    traces = InMemoryTraceStore()
    conversations = ConversationStore(tmp_path / "conversations.db")

    class FakeEldercareChatAgent:
        async def run(self, **kwargs):
            assert kwargs["message"] == "查看张爷爷今天的状态"
            assert kwargs["role"] == "viewer"
            return {
                "status": "succeeded",
                "response": "张爷爷当前心率82bpm。",
                "plan": {
                    "intent": "query",
                    "resident_id": "elderly-001",
                    "actions": [{"name": "read_health_summary"}],
                },
            }

    monkeypatch.setattr(app_module, "_eldercare_store", store)
    monkeypatch.setattr(app_module, "_eldercare_chat_agent", FakeEldercareChatAgent())
    monkeypatch.setattr(app_module, "_v2_trace_store", traces)
    monkeypatch.setattr(app_module, "_v2_conversation_store", conversations)

    client = TestClient(app_module.app)
    created = client.post("/api/v2/tasks", json={
        "message": "查看张爷爷今天的状态",
        "role": "viewer",
    })
    task_id = created.json()["task_id"]
    task = client.get(f"/api/v2/tasks/{task_id}").json()
    events = client.get(f"/api/v2/tasks/{task_id}/events").json()["events"]
    turns = client.get(
        f"/api/v2/conversations/{created.json()['conversation_id']}/turns"
    ).json()["turns"]

    assert created.status_code == 202
    assert task["status"] == "succeeded"
    assert task["result"]["response"] == "张爷爷当前心率82bpm。"
    assert any(event["type"] == "agent.plan.completed" for event in events)
    assert turns[0]["assistant_message"] == "张爷爷当前心率82bpm。"


def test_device_inventory_and_alert_acknowledgement(monkeypatch, tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        environment_device_id="ac-002",
    ))
    risk = store.create_risk(RiskEvent(
        resident_id="elderly-001",
        event_type="fall_detected",
        severity="critical",
        value=True,
        message="疑似跌倒",
    ))

    class FakeGateway:
        async def list_devices(self):
            return [{
                "id": "ac-002",
                "title": "A-101 房间空调",
                "actions": [{"name": "setTemperature"}],
            }]

        async def query_state(self, device_id):
            return {"device_id": device_id, "targetTemp": 24}

    monkeypatch.setattr(app_module, "_eldercare_store", store)
    monkeypatch.setattr(app_module, "_v2_gateway", FakeGateway())
    client = TestClient(app_module.app)

    inventory = client.get(
        "/api/v2/eldercare/residents/elderly-001/devices"
    )
    acknowledged = client.post(
        f"/api/v2/eldercare/events/{risk.event_id}/acknowledge"
    )

    assert inventory.status_code == 200
    assert len(inventory.json()["devices"]) == 4
    assert inventory.json()["devices"][-1]["transport"] == "WoT TD / MCP"
    assert inventory.json()["devices"][-1]["state"]["targetTemp"] == 24
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "handled"
