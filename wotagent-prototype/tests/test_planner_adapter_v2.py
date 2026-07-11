"""Single-point tests for Function Calling to TaskPlan conversion."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wotagent.agent_graph import LangChainTaskPlanner, capability_context


class FakeRunnable:
    def __init__(self, arguments):
        self.arguments = arguments
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(tool_calls=[{"name": "generate_plan", "args": self.arguments}])


def test_parses_valid_function_call_into_typed_plan() -> None:
    runnable = FakeRunnable({
        "intent": "control",
        "summary": "turn on light",
        "steps": [{
            "id": "s1",
            "kind": "action",
            "device_id": "light-001",
            "action": "turnOn",
            "postconditions": [{"property": "on", "operator": "eq", "value": True}],
        }],
    })

    plan = asyncio.run(LangChainTaskPlanner(runnable)({"message": "打开客厅灯"}))

    assert plan.intent == "control"
    assert plan.steps[0].device_id == "light-001"
    assert plan.steps[0].postconditions[0].value is True
    assert "Available devices" in runnable.messages[0][1]


def test_invalid_dependency_is_rejected_before_graph_execution() -> None:
    runnable = FakeRunnable({
        "intent": "control",
        "steps": [{
            "id": "s1",
            "kind": "action",
            "device_id": "light-001",
            "action": "turnOn",
            "depends_on": ["missing"],
        }],
    })

    with pytest.raises(ValidationError, match="unknown dependencies"):
        asyncio.run(LangChainTaskPlanner(runnable)({"message": "打开灯"}))


def test_missing_tool_call_is_rejected() -> None:
    class NoToolRunnable:
        async def ainvoke(self, messages):
            return SimpleNamespace(tool_calls=[], additional_kwargs={})

    with pytest.raises(ValueError, match="no generate_plan"):
        asyncio.run(LangChainTaskPlanner(NoToolRunnable())({"message": "打开灯"}))


def test_capability_context_exposes_registered_devices_without_full_td_noise() -> None:
    devices = capability_context()

    light = next(item for item in devices if item["device_id"] == "light-001")
    assert "turnOn" in light["actions"]
    assert "on" in light["properties"]
    assert "forms" not in light
