from __future__ import annotations

import asyncio
from typing import Any

import pytest

from wotagent.domain import DeploymentProfile, DomainAccessDenied
from wotagent.domains.smart_home import SmartHomeDomainPlugin


class FakeGateway:
    def __init__(self) -> None:
        self.states = {
            "smoke-sensor-001": {
                "smokeLevel": 0,
                "alarm": False,
                "observedAt": "2026-07-19T00:00:00Z",
            },
            "camera-001": {"online": True, "recording": False},
            "door-lock-001": {"locked": False, "tamperAlarm": False},
            "smart-meter-001": {
                "powerWatts": 1200,
                "energyKwhToday": 5.2,
            },
        }
        self.calls: list[dict[str, Any]] = []

    async def list_devices(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "smoke-sensor-001",
                "title": "Hall Smoke Sensor",
                "location": "hall",
                "actions": [{"name": "updateSmokeState"}],
            },
            {
                "id": "camera-001",
                "title": "Hall Security Camera",
                "location": "hall",
                "actions": [{"name": "startRecording"}],
            },
            {
                "id": "eldercare-only-device",
                "title": "Out Of Profile",
                "location": "A-101",
                "actions": [],
            },
        ]

    async def query_state(self, device_id: str) -> dict[str, Any]:
        return dict(self.states[device_id])

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append({
            "device_id": device_id,
            "action": action,
            "parameters": parameters,
            "idempotency_key": idempotency_key,
        })
        if action == "updateSmokeState":
            self.states[device_id].update(parameters)
        elif action == "startRecording":
            self.states[device_id]["recording"] = True
        elif action == "lock":
            self.states[device_id]["locked"] = True
        elif action == "updateEnergyState":
            self.states[device_id].update(parameters)
        return {"success": True}


def make_operations() -> tuple[SmartHomeDomainPlugin, FakeGateway]:
    gateway = FakeGateway()
    profile = DeploymentProfile(
        profile_id="villa",
        domain_id="smart_home",
        site_id="villa-demo",
        title="Villa",
        system_prompt="Villa test profile.",
        spaces=["hall", "entrance"],
        device_ids=[
            "smoke-sensor-001",
            "camera-001",
            "door-lock-001",
            "smart-meter-001",
        ],
        enabled_agents=[
            "security_agent",
            "environment_agent",
            "energy_agent",
            "housekeeping_agent",
            "device_ops_agent",
        ],
    )
    return (
        SmartHomeDomainPlugin(
            get_gateway=lambda: gateway,
            get_profile=lambda: profile,
        ),
        gateway,
    )


def test_manifest_exposes_butler_security_and_monitor_subjects() -> None:
    operations, _ = make_operations()

    actors = {
        actor.actor_id: actor
        for actor in operations.manifest().actors
    }

    assert actors["butler_run_supervisor"].llm_driven is True
    assert actors["security_agent"].label == "安防处置 Agent"
    assert actors["villa-rule-monitor"].llm_driven is False
    assert "security_agent" in actors["butler_run_supervisor"].delegates_to


def test_villa_resources_are_profile_scoped() -> None:
    operations, _ = make_operations()

    resources = asyncio.run(
        operations.resources("villa-demo", "JH-1")
    )

    assert {
        item["resource_id"]
        for item in resources
    } == {"smoke-sensor-001", "camera-001"}
    assert all(item["status"] == "online" for item in resources)


def test_operator_smoke_scenario_executes_td_action_and_emits_event() -> None:
    operations, gateway = make_operations()

    action = asyncio.run(
        operations.simulate(
            "villa-demo",
            "JH-1",
            "smoke_alarm",
        )
    )
    events = operations.events("villa-demo", "JH-1")

    assert gateway.calls[0]["device_id"] == "smoke-sensor-001"
    assert gateway.calls[0]["action"] == "updateSmokeState"
    assert gateway.calls[0]["parameters"]["alarm"] is True
    assert action["subject_id"] == "villa-demo"
    assert action["execution_mode"] == "wot_mcp"
    assert action["verified"] is True
    assert events[0]["event_type"] == "smoke_alarm"
    assert events[0]["severity"] == "high"


def test_security_patrol_uses_td_required_duration() -> None:
    operations, gateway = make_operations()

    asyncio.run(
        operations.simulate(
            "villa-demo",
            "JH-1",
            "security_patrol",
        )
    )

    assert gateway.calls[0]["parameters"] == {"durationSeconds": 60}


def test_viewer_can_read_but_cannot_execute_or_acknowledge() -> None:
    operations, _ = make_operations()

    assert operations.list_subjects("JH-2")[0]["metadata"]["access"] == "viewer"
    asyncio.run(operations.resources("villa-demo", "JH-2"))

    with pytest.raises(DomainAccessDenied):
        asyncio.run(
            operations.simulate(
                "villa-demo",
                "JH-2",
                "smoke_alarm",
            )
        )

    asyncio.run(operations.simulate("villa-demo", "JH-1", "smoke_alarm"))
    event_id = operations.events("villa-demo", "JH-1")[0]["event_id"]
    with pytest.raises(DomainAccessDenied):
        operations.acknowledge(event_id, "JH-2")


def test_operator_can_acknowledge_generated_event() -> None:
    operations, _ = make_operations()
    event = operations._emit_event(
        "smoke_alarm",
        "high",
        "Smoke",
        "smoke-sensor-001",
    )

    result = operations.acknowledge(event["event_id"], "JH-1")

    assert result["status"] == "acknowledged"
