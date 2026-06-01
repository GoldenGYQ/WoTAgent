"""Tests for the WoTAgent Pydantic schema models."""

from __future__ import annotations

from wotagent.models import (
    DeviceStateResponse,
    DeviceActionResponse,
    DeviceListEntry,
    DeviceListResponse,
    Objective,
    TaskSpec,
    Plan,
    PlanStep,
    ExecutionResult,
    EnvironmentEvent,
)


class TestDeviceModels:
    """Tests for Pydantic device state/action models."""
    def test_device_state_response_defaults(self):
        """Device state response includes default fields."""
        resp = DeviceStateResponse(device_id="light-001")
        assert resp.success is True
        assert resp.device_id == "light-001"
        assert resp.state == {}
        assert resp.capabilities == []

    def test_device_action_response_roundtrip(self):
        """Action response serializes and deserializes."""
        resp = DeviceActionResponse(
            success=True,
            device="Living Room Light",
            device_id="light-001",
            action="turnOn",
            parameters={"brightness": 80},
            message="Light turned on",
        )
        d = resp.model_dump()
        assert d["success"] is True
        assert d["device_id"] == "light-001"
        assert d["parameters"]["brightness"] == 80

    def test_device_list_entry(self):
        """Device list entry includes required metadata."""
        entry = DeviceListEntry(
            id="ac-001",
            title="Living Room AC",
            location="living_room",
            capabilities=["temperature", "mode"],
            actions=["turnOn", "turnOff", "setTemperature"],
            properties=["power", "currentTemperature"],
            state={"power": True, "currentTemperature": 25.0},
        )
        assert entry.id == "ac-001"
        assert entry.state["currentTemperature"] == 25.0

    def test_device_list_response(self):
        """Device list response wraps entries correctly."""
        entry = DeviceListEntry(id="light-001", title="Light", location="parlor")
        resp = DeviceListResponse(devices=[entry])
        assert resp.count == 1
        assert resp.devices[0].id == "light-001"


class TestLegacySchemas:
    """Tests for legacy schema models."""
    def test_objective(self):
        """Objective model has default values."""
        obj = Objective(action="set", attribute="brightness", location="parlor", direction="up")
        assert obj.action == "set"

    def test_task_spec_min_length(self):
        """Task spec enforces minimum content length."""
        obj = Objective(action="set", attribute="temp", location="bedroom", direction="down")
        spec = TaskSpec(objectives=[obj])
        assert len(spec.objectives) == 1

    def test_plan_step_optional_fields(self):
        """Plan step optional fields are None by default."""
        step = PlanStep(role="executor", capability_domain="lighting")
        assert step.device_hint is None
        assert step.operation is None

    def test_plan_default_id(self):
        """Plan generates default ID when omitted."""
        obj = Objective(action="set", attribute="brightness", location="parlor", direction="up")
        spec = TaskSpec(objectives=[obj])
        step = PlanStep(role="executor", capability_domain="lighting")
        plan = Plan(steps=[step])
        assert plan.plan_id
        assert len(plan.steps) == 1

    def test_execution_result(self):
        """Execution result stores output and status."""
        res = ExecutionResult(success=True, message="Completed")
        assert res.applied == []

    def test_environment_event(self):
        """Environment event carries source and data."""
        evt = EnvironmentEvent(state={"temp": 25}, rules=["rule1"], triggered=False)
        assert evt.triggered is False
