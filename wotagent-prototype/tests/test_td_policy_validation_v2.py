"""TD parameter contract tests at the policy boundary."""

from __future__ import annotations

from wotagent.domain import PlanStep
from wotagent.policy import PolicyEngine
from wotagent.wot import get_device_by_id


def action_step(action: str, parameters: dict) -> PlanStep:
    return PlanStep(
        id="s1",
        kind="action",
        device_id="light-001",
        action=action,
        input=parameters,
    )


def test_legacy_scalar_td_is_normalized_to_gateway_parameter_object() -> None:
    td = get_device_by_id("light-001")
    action = next(item for item in td.actions if item.name == "setBrightness")

    assert action.input_schema["type"] == "object"
    assert action.input_schema["required"] == ["brightness"]
    assert action.input_schema["properties"]["brightness"]["maximum"] == 100


def test_valid_brightness_parameter_is_allowed() -> None:
    decision = PolicyEngine().evaluate("operator", action_step(
        "setBrightness", {"brightness": 60}
    ))

    assert decision.effect == "allow"


def test_out_of_range_parameter_is_denied_before_gateway() -> None:
    decision = PolicyEngine().evaluate("operator", action_step(
        "setBrightness", {"brightness": 120}
    ))

    assert decision.effect == "deny"
    assert decision.reason_code == "PARAMETER_INVALID"


def test_missing_or_unknown_parameter_is_denied() -> None:
    missing = PolicyEngine().evaluate("operator", action_step("setBrightness", {}))
    unknown = PolicyEngine().evaluate("operator", action_step(
        "setBrightness", {"level": 60}
    ))

    assert missing.reason_code == "PARAMETER_INVALID"
    assert unknown.reason_code == "PARAMETER_INVALID"


def test_parameterless_action_rejects_extra_values() -> None:
    allowed = PolicyEngine().evaluate("operator", action_step("turnOn", {}))
    denied = PolicyEngine().evaluate("operator", action_step("turnOn", {"on": True}))

    assert allowed.effect == "allow"
    assert denied.reason_code == "PARAMETER_INVALID"


def test_known_actions_receive_deterministic_postconditions_when_planner_omits_them() -> None:
    turn_on = action_step("turnOn", {})
    brightness = action_step("setBrightness", {"brightness": 60})

    assert turn_on.postconditions[0].model_dump() == {
        "property": "on", "operator": "eq", "value": True,
    }
    assert brightness.postconditions[0].model_dump() == {
        "property": "brightness", "operator": "eq", "value": 60,
    }


def test_llm_cannot_override_standard_action_semantics_with_conflicting_postcondition() -> None:
    step = PlanStep(
        id="s1",
        kind="action",
        device_id="light-001",
        action="turnOn",
        postconditions=[{"property": "on", "value": False}],
    )

    assert step.postconditions[0].value is True
