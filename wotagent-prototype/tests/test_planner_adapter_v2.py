"""Single-point tests for Function Calling to TaskPlan conversion."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wotagent.agent_graph import LangChainTaskPlanner, capability_context
from wotagent.telemetry import InMemoryTraceStore


class FakeRunnable:
    def __init__(self, arguments):
        self.arguments = arguments
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(
            tool_calls=[{"name": "generate_plan", "args": self.arguments}],
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "output_token_details": {"reasoning": 8},
            },
            response_metadata={"model_name": "fake-planner"},
        )


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
    assert "turnOn" in {action["name"] for action in light["actions"]}
    brightness = next(action for action in light["actions"] if action["name"] == "setBrightness")
    assert brightness["input_schema"]["required"] == ["brightness"]
    assert "on" in light["properties"]
    assert "living room" in light["location_aliases"]
    assert "forms" not in light


def test_capability_context_selects_only_relevant_room_and_device_type() -> None:
    devices = capability_context("把客厅灯打开")

    assert [item["device_id"] for item in devices] == ["light-001"]


def test_capability_context_falls_back_to_all_devices_for_generic_request() -> None:
    assert len(capability_context("查询所有设备状态")) == len(capability_context())


def test_planner_records_usage_and_safe_reasoning_summary() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-planner", session_id="sess", request_id="req")
        runnable = FakeRunnable({
            "intent": "control",
            "summary": "打开客厅灯",
            "steps": [{
                "id": "s1",
                "kind": "action",
                "device_id": "light-001",
                "action": "turnOn",
            }],
        })
        await LangChainTaskPlanner(runnable, trace_store=store)({
            "task_id": "task-planner",
            "message": "打开客厅灯",
        })
        return await store.get("task-planner"), await store.events("task-planner")

    record, events = asyncio.run(run())

    assert record.usage.model_calls == 1
    assert record.usage.total_tokens == 120
    completed = next(event for event in events if event.type == "model.request.completed")
    assert completed.data["reasoning_tokens"] == 8
    summary = next(event for event in events if event.type == "model.reasoning.summary")
    assert summary.data == {"summary": "打开客厅灯", "intent": "control", "step_count": 1}
    assert all("reasoning_content" not in event.data for event in events)
