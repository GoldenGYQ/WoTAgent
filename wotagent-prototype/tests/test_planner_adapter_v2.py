"""Single-point tests for Function Calling to TaskPlan conversion."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wotagent.agent_graph import LangChainTaskPlanner, capability_context
from wotagent.agent_graph.planner import ConditionalPlanReview
from wotagent.prompting import profile_for
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


class FakeThinkingRunnable:
    def __init__(self, analysis: str = "Resolve the request as a chat response.") -> None:
        self.analysis = analysis
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(
            content=self.analysis,
            usage_metadata={"input_tokens": 60, "output_tokens": 15, "total_tokens": 75},
            response_metadata={"model_name": "fake-thinking-planner"},
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
    assert "分配的智能体：environment_agent" in runnable.messages[0][1]
    assert "light-001" not in runnable.messages[0][1]
    assert "light-001" in runnable.messages[2][1]


def test_thinking_request_uses_dedicated_planner_and_marks_trace() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-thinking", session_id="sess", request_id="req")
        direct = FakeRunnable({"intent": "chat", "summary": "direct", "steps": []})
        deep = FakeThinkingRunnable("Classify this request as chat.")
        plan = await LangChainTaskPlanner(
            direct,
            trace_store=store,
            thinking_runnable=deep,
        )({"task_id": "task-thinking", "message": "分析一下", "thinking": True})
        return plan, direct, deep, await store.events("task-thinking")

    plan, direct, deep, events = asyncio.run(run())

    assert plan.summary == "direct"
    assert "Private planning analysis" in direct.messages[-1][1]
    assert "Classify this request as chat." in direct.messages[-1][1]
    assert deep.messages is not None
    assert "分配的智能体：environment_agent" in deep.messages[0][1]
    assert "light-001" in deep.messages[2][1]
    completed = [event for event in events if event.type == "model.request.completed"]
    assert [event.data["purpose"] for event in completed] == ["task_analysis", "task_planning"]
    assert completed[0].data["model_thinking"] is True
    assert completed[1].data["model_thinking"] is False
    assert all("reasoning_content" not in event.data for event in events)
    started = next(
        event
        for event in events
        if event.type == "model.request.started"
        and event.data["purpose"] == "task_planning"
    )
    planning_completed = next(
        event
        for event in events
        if event.type == "model.request.completed"
        and event.data["purpose"] == "task_planning"
    )
    assert started.raw_data["messages"] == direct.messages
    assert planning_completed.raw_data["structured_plan"]["intent"] == "chat"
    assert "messages" not in started.data


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


def test_invalid_empty_control_plan_is_repaired_to_terminal_chat() -> None:
    async def run():
        class SequencedRunnable:
            def __init__(self) -> None:
                self.calls = 0
                self.messages = []

            async def ainvoke(self, messages):
                self.calls += 1
                self.messages.append(messages)
                arguments = (
                    {"intent": "control", "summary": "已经完成", "steps": []}
                    if self.calls == 1
                    else {"intent": "chat", "summary": "已经完成", "steps": []}
                )
                return SimpleNamespace(
                    tool_calls=[{"name": "generate_plan", "args": arguments}],
                    usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    response_metadata={"model_name": "fake-planner"},
                )

        store = InMemoryTraceStore()
        await store.create(task_id="task-repair", session_id="sess", request_id="req")
        runnable = SequencedRunnable()
        plan = await LangChainTaskPlanner(runnable, trace_store=store)({
            "task_id": "task-repair",
            "message": "继续执行条件任务",
        })
        return runnable, plan, await store.events("task-repair")

    runnable, plan, events = asyncio.run(run())

    failed = next(event for event in events if event.type == "plan.validation.failed")
    repaired = [event for event in events if event.type == "model.request.completed"]

    assert failed.data["raw_plan"] == {"intent": "control", "summary": "已经完成", "steps": []}
    assert repaired[0].data["raw_plan"] == {"intent": "control", "summary": "已经完成", "steps": []}
    assert repaired[1].data["raw_plan"] == {"intent": "chat", "summary": "已经完成", "steps": []}
    assert "repair_prompt" not in repaired[0].data["raw_plan"]
    assert runnable.calls == 2
    assert plan.intent == "chat"
    assert plan.steps == []
    assert "query/control intents require at least one executable step" in (
        runnable.messages[1][-1][1]
    )


def test_invalid_empty_control_plan_records_prompt_snapshot_for_repair() -> None:
    class SequencedRunnable:
        def __init__(self) -> None:
            self.calls = 0
            self.messages = []

        async def ainvoke(self, messages):
            self.calls += 1
            self.messages.append(messages)
            arguments = (
                {"intent": "control", "summary": "已经完成", "steps": []}
                if self.calls == 1
                else {"intent": "chat", "summary": "已经完成", "steps": []}
            )
            return SimpleNamespace(
                tool_calls=[{"name": "generate_plan", "args": arguments}],
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={"model_name": "fake-planner"},
            )

    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-repair-prompt", session_id="sess", request_id="req")
        runnable = SequencedRunnable()
        await LangChainTaskPlanner(runnable, trace_store=store)({
            "task_id": "task-repair-prompt",
            "message": "继续执行条件任务",
        })
        return await store.events("task-repair-prompt")

    events = asyncio.run(run())
    started = [event for event in events if event.type == "model.request.started"]
    assert "prompt_snapshot" in started[0].data
    assert "runtime_context" in started[0].data["prompt_snapshot"]
    assert "repair_prompt" in started[1].data["prompt_snapshot"]


def test_repeated_empty_control_after_execution_is_safely_normalized() -> None:
    runnable = FakeRunnable({
        "intent": "control",
        "summary": "条件分支已完成，没有剩余动作",
        "steps": [],
    })

    plan = asyncio.run(LangChainTaskPlanner(runnable)({
        "message": "继续执行条件任务",
        "execution_history": [{"status": "succeeded"}],
    }))

    assert plan.intent == "chat"
    assert plan.steps == []
    assert plan.summary == "条件分支已完成，没有剩余动作"


def test_intent_is_canonicalized_from_action_steps() -> None:
    runnable = FakeRunnable({
        "intent": "query",
        "summary": "先查询再执行开灯",
        "steps": [{
            "id": "s1",
            "kind": "action",
            "device_id": "light-001",
            "action": "turnOn",
        }],
    })

    plan = asyncio.run(LangChainTaskPlanner(runnable)({"message": "打开灯"}))

    assert plan.intent == "control"
    assert plan.steps[0].action == "turnOn"


def test_intent_canonicalization_is_recorded_in_trace() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-intent-normalize", session_id="sess", request_id="req")
        runnable = FakeRunnable({
            "intent": "clarify",
            "summary": "读取当前状态",
            "steps": [{
                "id": "q1",
                "kind": "query",
                "device_id": "light-001",
                "action": "queryState",
            }],
        })
        plan = await LangChainTaskPlanner(runnable, trace_store=store)({
            "task_id": "task-intent-normalize",
            "message": "看看灯现在是什么状态",
        })
        return plan, await store.events("task-intent-normalize")

    plan, events = asyncio.run(run())

    normalized = next(event for event in events if event.type == "plan.normalized")
    assert plan.intent == "query"
    assert normalized.data["from_intent"] == "clarify"
    assert normalized.data["to_intent"] == "query"


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

    assert len(lights) == 9
    assert all(
        {"turnOn", "turnOff"}.issubset({action["name"] for action in light["actions"]})
        for light in lights
    )


def test_capability_context_does_not_guess_semantics_with_keywords() -> None:
    devices = capability_context("把客厅灯打开")

    assert len(devices) == len(capability_context())
    assert "light-001" in {item["device_id"] for item in devices}
    assert "ac-001" in {item["device_id"] for item in devices}


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
    capability_prompt = runnable.messages[2][1]
    assert "remote-esp32-1" not in system_prompt
    assert "remote-esp32-1" in capability_prompt
    assert "light-001" not in capability_prompt


def test_planner_uses_the_assigned_professional_agent_profile() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "ok", "steps": []})

    asyncio.run(LangChainTaskPlanner(
        runnable,
        agent_profile=profile_for("security_agent"),
    )({"message": "检查门锁"}))

    system_prompt = runnable.messages[0][1]
    assert "分配的智能体：security_agent" in system_prompt
    assert "wot.security" in system_prompt
    assert "wot.energy" not in system_prompt


def test_observe_act_planner_receives_observations_and_all_relevant_devices() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "done", "steps": []})
    devices = [
        {
            "device_id": f"light-{index:03d}",
            "title": f"Light {index}",
            "location": f"room-{index}",
            "device_type": "light",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        }
        for index in range(1, 8)
    ] + [
        {
            "device_id": "ac-001",
            "title": "Air Conditioner",
            "location": "living_room",
            "device_type": "air_conditioner",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        },
        {
            "device_id": "humidifier-001",
            "title": "Humidifier",
            "location": "bedroom1",
            "device_type": "humidifier",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        },
        {
            "device_id": "fan-001",
            "title": "Fan",
            "location": "study",
            "device_type": "fan",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        },
    ]

    asyncio.run(LangChainTaskPlanner(runnable)({
        "message": "检查所有灯，按奇偶打开空调或加湿器，再决定是否打开风扇",
        "context": {
            "devices": devices,
            "agent_loop": {
                "execution_mode": "observe_act_loop",
                "iteration": 2,
                "observations": [{
                    "device_id": "light-001",
                    "result": {"on": True},
                }],
            },
        },
    }))

    runtime_prompt = runnable.messages[1][1]
    capability_prompt = runnable.messages[2][1]
    assert '"agent_loop"' in runtime_prompt
    assert '"light-001"' in runtime_prompt
    assert all(device["device_id"] in capability_prompt for device in devices)


def test_observe_act_planner_keeps_all_scoped_devices_for_follow_up_control() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "done", "steps": []})
    devices = [
        {
            "device_id": "environment-sensor-001",
            "title": "Environment Sensor",
            "location": "bedroom1",
            "device_type": "environment_sensor",
            "actions": [{"name": "updateEnvironment", "input_schema": {"type": "object"}}],
            "properties": ["temperature"],
        },
        {
            "device_id": "ac-002",
            "title": "Bedroom AC",
            "location": "bedroom1",
            "device_type": "air_conditioner",
            "actions": [{"name": "setTemperature", "input_schema": {"type": "object"}}],
            "properties": ["targetTemp", "on"],
        },
    ]

    asyncio.run(LangChainTaskPlanner(runnable)({
        "message": "对张爷爷房间（A-101）执行降温操作，将温度降至28℃以下",
        "context": {
            "devices": devices,
            "agent_loop": {
                "execution_mode": "observe_act_loop",
                "iteration": 2,
                "observations": [{
                    "device_id": "environment-sensor-001",
                    "result": {"temperature": 30.5},
                }],
            },
        },
    }))

    capability_prompt = runnable.messages[2][1]
    assert '"environment-sensor-001"' in capability_prompt
    assert '"ac-002"' in capability_prompt


def test_conditional_plan_reviewer_blocks_inactive_branch_and_repairs_plan() -> None:
    class SequencedPlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            device_id = "ac-001" if self.calls == 1 else "humidifier-001"
            return SimpleNamespace(
                tool_calls=[{
                    "name": "generate_plan",
                    "args": {
                        "intent": "control",
                        "summary": "执行当前条件分支",
                        "steps": [{
                            "id": f"action-{self.calls}",
                            "kind": "action",
                            "device_id": device_id,
                            "action": "turnOn",
                        }],
                    },
                }],
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={"model_name": "fake-planner"},
            )

    class FakeReviewer:
        def __init__(self) -> None:
            self.calls = 0

        async def review(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return ConditionalPlanReview(
                    approved=False,
                    selected_branch="even",
                    violations=["air conditioner belongs to the inactive odd branch"],
                )
            return ConditionalPlanReview(
                approved=True,
                selected_branch="even",
            )

    devices = [
        {
            "device_id": "ac-001",
            "title": "AC",
            "location": "living_room",
            "device_type": "air_conditioner",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        },
        {
            "device_id": "humidifier-001",
            "title": "Humidifier",
            "location": "bedroom1",
            "device_type": "humidifier",
            "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
            "properties": ["on"],
        },
    ]
    runnable = SequencedPlanner()
    reviewer = FakeReviewer()

    plan = asyncio.run(LangChainTaskPlanner(
        runnable,
        plan_reviewer=reviewer,
    )({
        "message": "开灯数为奇数开空调，偶数开加湿器",
        "execution_history": [{"status": "succeeded"}],
        "context": {
            "devices": devices,
            "agent_loop": {
                "original_objective": "开灯数为奇数开空调，偶数开加湿器",
                "observations": [
                    {"device_id": "light-001", "result": {"on": True}},
                    {"device_id": "light-002", "result": {"on": True}},
                ],
            },
        },
    }))

    assert runnable.calls == 2
    assert reviewer.calls == 2
    assert plan.steps[0].device_id == "humidifier-001"


def test_conditional_plan_reviewer_runs_for_query_intent_with_action_steps() -> None:
    class SequencedPlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            arguments = (
                {
                    "intent": "query",
                    "summary": "先查状态，再决定是否开灯",
                    "steps": [{
                        "id": "q1",
                        "kind": "query",
                        "device_id": "light-001",
                        "action": "queryState",
                    }, {
                        "id": "s1",
                        "kind": "action",
                        "device_id": "light-001",
                        "action": "turnOn",
                        "depends_on": ["q1"],
                    }],
                }
                if self.calls == 1
                else {
                    "intent": "query",
                    "summary": "只需读取当前状态",
                    "steps": [{
                        "id": "q1",
                        "kind": "query",
                        "device_id": "light-001",
                        "action": "queryState",
                    }],
                }
            )
            return SimpleNamespace(
                tool_calls=[{"name": "generate_plan", "args": arguments}],
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={"model_name": "fake-planner"},
            )

    class FakeReviewer:
        def __init__(self) -> None:
            self.calls = 0

        async def review(self, **kwargs):
            self.calls += 1
            plan = kwargs["plan"]
            if any(step.kind == "action" for step in plan.steps):
                return ConditionalPlanReview(
                    approved=False,
                    selected_branch="query_only",
                    violations=["action step should not run after the observed branch"],
                )
            return ConditionalPlanReview(approved=True, selected_branch="query_only")

    runnable = SequencedPlanner()
    reviewer = FakeReviewer()

    plan = asyncio.run(LangChainTaskPlanner(
        runnable,
        plan_reviewer=reviewer,
    )({
        "message": "看看灯是不是亮着，如果没亮就打开",
        "execution_history": [{"status": "succeeded"}],
        "context": {
            "devices": [{
                "device_id": "light-001",
                "title": "Light",
                "location": "living_room",
                "device_type": "light",
                "actions": [{"name": "turnOn", "input_schema": {"type": "object"}}],
                "properties": ["on"],
            }],
            "agent_loop": {
                "original_objective": "看看灯是不是亮着，如果没亮就打开",
                "observations": [{"device_id": "light-001", "result": {"on": True}}],
            },
        },
    }))

    assert runnable.calls == 2
    assert reviewer.calls == 1
    assert plan.intent == "query"
    assert [step.kind for step in plan.steps] == ["query"]


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
    started = next(event for event in events if event.type == "model.request.started")
    assert started.data["agent_id"] == "environment_agent"
    assert started.data["system_characters"] > 0
    assert started.data["capability_context_characters"] > 0
    assert started.data["selected_device_ids"] == started.data["all_device_ids"]
    assert "light-001" in started.data["all_device_ids"]
    assert started.data["prompt_snapshot"]["user_prompt"] == "打开客厅灯"
    prompt_device_ids = {
        item["device_id"]
        for item in started.data["prompt_snapshot"]["capabilities"]
        if isinstance(item, dict)
    }
    assert len(prompt_device_ids) > 1
    assert completed.data["raw_plan"]["intent"] == "control"
    assert completed.data["raw_plan"]["steps"][0]["device_id"] == "light-001"
    summary = next(event for event in events if event.type == "model.reasoning.summary")
    assert summary.data == {"summary": "打开客厅灯", "intent": "control", "step_count": 1}
    assert all("reasoning_content" not in event.data for event in events)


def test_pending_goal_is_context_for_model_decision_not_keyword_resumed() -> None:
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
    runnable = FakeRunnable(pending_plan)

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
    assert runnable.messages is not None
    assert "pending_goal" in str(runnable.messages)


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

    assert '"current_role":"operator"' in runnable.messages[1][1]
    assert "规划不等于授权" in runnable.messages[0][1]


def test_elliptical_followup_uses_history_without_python_keyword_filtering() -> None:
    runnable = FakeRunnable({"intent": "chat", "summary": "ok", "steps": []})

    asyncio.run(LangChainTaskPlanner(runnable)({
        "message": "全关掉",
        "conversation_history": [
            {"role": "user", "content": "我们一共有多少灯"},
            {"role": "assistant", "content": "一共有7盏灯"},
        ],
    }))

    capability_prompt = runnable.messages[2][1]
    assert "light-001" in capability_prompt
    assert "light-007" in capability_prompt
    assert "ac-001" in capability_prompt
    assert any("7" in str(message[1]) for message in runnable.messages)


def test_planner_does_not_reinterpret_power_direction_with_string_rules() -> None:
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
    plan = asyncio.run(LangChainTaskPlanner(
        runnable,
        capability_provider=capabilities,
    )({"message": "全关掉"}))

    assert plan.steps[0].action == "turnOn"
    assert runnable.calls == 1


def test_mixed_power_directions_do_not_trigger_false_reverse_validation() -> None:
    class MixedRunnable:
        async def ainvoke(self, _messages):
            return SimpleNamespace(
                tool_calls=[{"name": "generate_plan", "args": {
                    "intent": "control",
                    "steps": [{
                        "id": "off-light",
                        "kind": "action",
                        "device_id": "light-007",
                        "action": "turnOff",
                    }, {
                        "id": "on-exhaust",
                        "kind": "action",
                        "device_id": "exhaust-003",
                        "action": "turnOn",
                    }],
                }}],
                usage_metadata={},
                response_metadata={"model_name": "fake-planner"},
            )

    async def capabilities():
        empty = {"type": "object", "additionalProperties": False}
        return [
            {
                "device_id": "light-007",
                "title": "Bathroom 2 Light",
                "location": "bathroom2",
                "device_type": "light",
                "actions": [
                    {"name": "turnOn", "input_schema": empty},
                    {"name": "turnOff", "input_schema": empty},
                ],
                "properties": ["on"],
            },
            {
                "device_id": "exhaust-003",
                "title": "Bathroom 2 Exhaust",
                "location": "bathroom2",
                "device_type": "exhaust",
                "actions": [
                    {"name": "turnOn", "input_schema": empty},
                    {"name": "turnOff", "input_schema": empty},
                ],
                "properties": ["on"],
            },
        ]

    plan = asyncio.run(LangChainTaskPlanner(
        MixedRunnable(),
        capability_provider=capabilities,
    )({"message": "关闭 bathroom2 的灯，并打开 bathroom2 的排风"}))

    assert [step.action for step in plan.steps] == ["turnOff", "turnOn"]


def test_power_direction_guard_can_be_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    class OppositeRunnable:
        async def ainvoke(self, _messages):
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
            "device_type": "light",
            "actions": [
                {"name": "turnOn", "input_schema": empty},
                {"name": "turnOff", "input_schema": empty},
            ],
            "properties": ["on"],
        }]

    monkeypatch.setenv("WOTAGENT_ENABLE_POWER_DIRECTION_GUARD", "0")
    plan = asyncio.run(LangChainTaskPlanner(
        OppositeRunnable(),
        capability_provider=capabilities,
    )({"message": "全关掉"}))

    assert plan.steps[0].action == "turnOn"
