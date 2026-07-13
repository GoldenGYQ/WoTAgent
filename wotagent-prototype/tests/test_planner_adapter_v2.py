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


def test_thinking_request_uses_dedicated_planner_and_marks_trace() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-thinking", session_id="sess", request_id="req")
        direct = FakeRunnable({"intent": "chat", "summary": "direct", "steps": []})
        deep = FakeRunnable({"intent": "chat", "summary": "deep", "steps": []})
        plan = await LangChainTaskPlanner(
            direct,
            trace_store=store,
            thinking_runnable=deep,
        )({"task_id": "task-thinking", "message": "分析一下", "thinking": True})
        return plan, direct, deep, await store.events("task-thinking")

    plan, direct, deep, events = asyncio.run(run())

    assert plan.summary == "deep"
    assert direct.messages is None
    assert deep.messages is not None
    completed = next(event for event in events if event.type == "model.request.completed")
    assert completed.data["thinking"] is True


def test_thinking_request_fails_when_server_capability_is_disabled() -> None:
    with pytest.raises(ValueError, match="thinking mode is not available"):
        asyncio.run(LangChainTaskPlanner(FakeRunnable({}))({
            "message": "分析一下",
            "thinking": True,
        }))


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


def test_all_simulated_lights_declare_symmetric_power_actions() -> None:
    lights = [item for item in capability_context() if "light" in item["title"].lower()]

    assert len(lights) == 7
    assert all(
        {"turnOn", "turnOff"}.issubset({action["name"] for action in light["actions"]})
        for light in lights
    )


def test_capability_context_selects_only_relevant_room_and_device_type() -> None:
    devices = capability_context("把客厅灯打开")

    assert [item["device_id"] for item in devices] == ["light-001"]


def test_capability_context_falls_back_to_all_devices_for_generic_request() -> None:
    assert len(capability_context("查询所有设备状态")) == len(capability_context())


def test_planner_uses_injected_mcp_capability_provider_as_source_of_truth() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "ok", "steps": []})

    async def capabilities():
        return [{
            "device_id": "remote-esp32-1",
            "title": "RemoteESP32Light",
            "location": "living_room",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        }]

    asyncio.run(LangChainTaskPlanner(
        runnable,
        capability_provider=capabilities,
    )({"message": "打开客厅灯"}))

    system_prompt = runnable.messages[0][1]
    assert "remote-esp32-1" in system_prompt
    assert "light-001" not in system_prompt


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


def test_explicit_retry_resumes_pending_plan_without_another_model_call() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "should not run", "steps": []})
    pending_plan = {
        "intent": "control",
        "summary": "关闭全部灯",
        "steps": [{
            "id": "off-1",
            "kind": "action",
            "device_id": "remote-light",
            "action": "turnOff",
        }],
    }

    async def capabilities():
        return [{
            "device_id": "remote-light",
            "title": "RemoteLight",
            "location": "living_room",
            "actions": [{
                "name": "turnOff",
                "input_schema": {"type": "object", "additionalProperties": False},
            }],
            "properties": ["on"],
        }]

    plan = asyncio.run(LangChainTaskPlanner(
        runnable,
        capability_provider=capabilities,
    )({
        "message": "现在呢",
        "context": {"runtime": {
            "current_role": "operator",
            "pending_goal": {"source_task_id": "old", "plan": pending_plan},
        }},
    }))

    assert plan.intent == "control"
    assert plan.steps[0].action == "turnOff"
    assert runnable.messages is None


def test_td_invalid_plan_is_repaired_once_before_policy_or_execution() -> None:
    class RepairingRunnable:
        def __init__(self) -> None:
            self.calls = 0
            self.inputs = []

        async def ainvoke(self, messages):
            self.calls += 1
            self.inputs.append(messages)
            action = "turnOff" if self.calls == 1 else "turnOn"
            return SimpleNamespace(
                tool_calls=[{"name": "generate_plan", "args": {
                    "intent": "control",
                    "steps": [{
                        "id": "s1",
                        "kind": "action",
                        "device_id": "remote-light",
                        "action": action,
                    }],
                }}],
                usage_metadata={},
                response_metadata={"model_name": "fake-planner"},
            )

    async def capabilities():
        return [{
            "device_id": "remote-light",
            "title": "RemoteLight",
            "location": "living_room",
            "actions": [{
                "name": "turnOn",
                "input_schema": {"type": "object", "additionalProperties": False},
            }],
            "properties": ["on"],
        }]

    runnable = RepairingRunnable()
    plan = asyncio.run(LangChainTaskPlanner(
        runnable,
        capability_provider=capabilities,
    )({"message": "打开灯", "role": "operator"}))

    assert runnable.calls == 2
    assert plan.steps[0].action == "turnOn"
    assert all(isinstance(message, tuple) for message in runnable.inputs[1])


def test_td_invalid_plan_fails_closed_after_one_repair() -> None:
    class AlwaysInvalidRunnable:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return SimpleNamespace(
                tool_calls=[{"name": "generate_plan", "args": {
                    "intent": "control",
                    "steps": [{
                        "id": "s1",
                        "kind": "action",
                        "device_id": "remote-light",
                        "action": "inventedAction",
                    }],
                }}],
                usage_metadata={},
                response_metadata={"model_name": "fake-planner"},
            )

    async def capabilities():
        return [{
            "device_id": "remote-light",
            "title": "RemoteLight",
            "location": "living_room",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        }]

    runnable = AlwaysInvalidRunnable()
    with pytest.raises(ValueError, match="after one repair"):
        asyncio.run(LangChainTaskPlanner(
            runnable,
            capability_provider=capabilities,
        )({"message": "打开灯"}))

    assert runnable.calls == 2


def test_runtime_role_is_exposed_as_fact_but_not_decided_by_planner() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "ok", "steps": []})

    asyncio.run(LangChainTaskPlanner(runnable)({
        "message": "现在是什么权限",
        "context": {"runtime": {"current_role": "operator"}},
    }))

    assert '"current_role": "operator"' in runnable.messages[0][1]
    assert "Never ask the user whether their role changed" in runnable.messages[0][1]


def test_elliptical_followup_inherits_recent_device_scope_for_capability_selection() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "ok", "steps": []})

    asyncio.run(LangChainTaskPlanner(runnable)({
        "message": "全关掉",
        "conversation_history": [
            {"role": "user", "content": "我们一共有多少灯"},
            {"role": "assistant", "content": "一共有7盏灯"},
        ],
    }))

    system = runnable.messages[0][1]
    assert "light-001" in system
    assert "light-007" in system
    assert "ac-001" not in system


def test_plan_repair_cannot_reverse_power_off_intent() -> None:
    class OppositeRunnable:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return SimpleNamespace(
                tool_calls=[{"name": "generate_plan", "args": {
                    "intent": "control",
                    "steps": [{
                        "id": "s1",
                        "kind": "action",
                        "device_id": "remote-light",
                        "action": "turnOn",
                    }],
                }}],
                usage_metadata={},
                response_metadata={"model_name": "fake-planner"},
            )

    async def capabilities():
        empty = {"type": "object", "additionalProperties": False}
        return [{
            "device_id": "remote-light",
            "title": "RemoteLight",
            "location": "living_room",
            "actions": [
                {"name": "turnOn", "input_schema": empty},
                {"name": "turnOff", "input_schema": empty},
            ],
            "properties": ["on"],
        }]

    runnable = OppositeRunnable()
    with pytest.raises(ValueError, match="reverses the requested power-off intent"):
        asyncio.run(LangChainTaskPlanner(
            runnable,
            capability_provider=capabilities,
        )({"message": "全关掉"}))

    assert runnable.calls == 2
