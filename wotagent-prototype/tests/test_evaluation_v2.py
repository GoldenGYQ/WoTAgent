"""Tests for external benchmark alignment and deterministic scoring."""

from __future__ import annotations

from wotagent.domain import PlanStep, TaskPlan
from wotagent.evaluation import HomeBenchAdapter, score_plan


def test_homebench_supported_case_maps_to_wot_device_and_action() -> None:
    case = HomeBenchAdapter().convert({
        "id": "sample",
        "input": "Set the brightness of the light to 60 in the living room.",
        "output": "''' living_room.light.set_brightness(60)'''",
        "type": "normal",
    })

    assert case.expected_actions[0].model_dump() == {
        "device_id": "light-001",
        "action": "setBrightness",
        "input": {"brightness": 60},
    }


def test_adapter_rejects_relative_or_unsupported_environment_cases() -> None:
    relative = HomeBenchAdapter().convert({
        "id": "relative", "input": "Increase the light by 20 percent.",
        "output": "''' living_room.light.set_brightness(60)'''", "type": "normal",
    })
    unsupported = HomeBenchAdapter().convert({
        "id": "curtain", "input": "Close the curtain.",
        "output": "''' living_room.curtain.close()'''", "type": "normal",
    })

    assert relative is None
    assert unsupported is None


def test_adapter_accepts_guest_bedroom_turn_off_declared_by_td() -> None:
    case = HomeBenchAdapter().convert({
        "id": "missing-action",
        "input": "Turn off the light in the guest bedroom.",
        "output": "''' guest_bedroom.light.turn_off()'''",
        "type": "normal",
    })

    assert case.expected_actions[0].model_dump() == {
        "device_id": "light-003",
        "action": "turnOff",
        "input": {},
    }


def test_plan_scoring_is_order_independent_but_argument_exact() -> None:
    adapter = HomeBenchAdapter()
    case = adapter.convert({
        "id": "multi",
        "input": "Turn on two lights.",
        "output": "''' living_room.light.turn_on(),study_room.light.turn_on()'''",
        "type": "multi2_normal",
    })
    exact = TaskPlan(intent="control", steps=[
        PlanStep(id="2", kind="action", device_id="light-004", action="turnOn"),
        PlanStep(id="1", kind="action", device_id="light-001", action="turnOn"),
    ])
    wrong = TaskPlan(intent="control", steps=[
        PlanStep(id="1", kind="action", device_id="light-001", action="turnOn"),
        PlanStep(id="2", kind="action", device_id="light-004", action="setBrightness", input={"brightness": 50}),
    ])

    assert score_plan(case, exact)["exact_match"] is True
    assert score_plan(case, wrong)["exact_match"] is False
