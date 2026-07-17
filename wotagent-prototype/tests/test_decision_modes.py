from __future__ import annotations

import asyncio

from wotagent.agent_graph import DecisionModeTaskPlanner
from wotagent.agentic_config import AgenticSettings
from wotagent.domains.eldercare import EldercareRuntime, EldercareStore, Resident, RiskEvent


class ShouldNotRunPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _state):
        self.calls += 1
        raise AssertionError("LLM planner should not run")


def test_simple_light_command_uses_rule_fast_path() -> None:
    primary = ShouldNotRunPlanner()
    plan = asyncio.run(DecisionModeTaskPlanner(
        primary,
        mode="hybrid",
    )({
        "message": "打开客厅灯",
        "context": {"devices": [{
            "device_id": "light-001",
            "title": "客厅灯",
            "location": "living_room",
            "actions": [
                {"name": "turnOn", "input_schema": {"type": "object"}},
                {"name": "turnOff", "input_schema": {"type": "object"}},
            ],
            "properties": ["on"],
        }]},
    }))

    assert primary.calls == 0
    assert plan.steps[0].action == "turnOn"
    assert plan.steps[0].device_id == "light-001"


def test_shadow_mode_executes_rule_actions_only_once(tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        caregiver="王护理员",
    ))
    runtime = EldercareRuntime(
        store,
        settings=AgenticSettings(
            decision_mode="hybrid",
            shadow_mode=True,
        ),
    )
    result = asyncio.run(runtime.process_event(RiskEvent(
        resident_id="elderly-001",
        event_type="fall_detected",
        severity="critical",
        value=True,
        message="疑似跌倒",
    )))

    assert result["status"] == "succeeded"
    assert result["shadow"]["real_execution"] == "rule_only"
    assert [item["action"] for item in store.list_actions()].count(
        "notify_caregiver"
    ) == 1
    assert [item["action"] for item in store.list_actions()].count(
        "create_care_task"
    ) == 1
