"""Tests for the SimuHome-to-WoTAgent semantic adapter."""

from __future__ import annotations

from wotagent.evaluation.simuhome import analyze_simuhome_episode, translate_matter_assertion


def test_translates_common_matter_attributes_to_canonical_actions() -> None:
    assert translate_matter_assertion("1.OnOff.OnOff", True).action == "turnOn"
    assert translate_matter_assertion("1.LevelControl.CurrentLevel", 30).parameters == {
        "brightness": 30,
    }
    assert translate_matter_assertion(
        "1.Thermostat.OccupiedCoolingSetpoint", 2350
    ).parameters == {"temperature": 23.5}
    assert translate_matter_assertion("1.Thermostat.SystemMode", 3).parameters == {
        "mode": "cool",
    }


def test_explicit_control_episode_can_be_td_ready() -> None:
    episode = {
        "meta": {"query_type": "qt3", "case": "feasible"},
        "eval": {"goals": [{
            "asserts": [
                {"attribute": "1.OnOff.OnOff", "value": True},
                {"attribute": "1.FanControl.PercentSetting", "value": 70},
            ],
        }]},
    }
    result = analyze_simuhome_episode(episode)
    assert result.td_action_coverage == 1.0
    assert result.td_action_ready is True


def test_temporal_episode_requires_workflow_runtime_even_when_actions_map() -> None:
    episode = {
        "meta": {"query_type": "qt4-1", "case": "feasible"},
        "eval": {"goals": [{
            "when": {"at_tick": 100},
            "targets": [{"asserts": [{"attribute": "1.OnOff.OnOff", "value": True}]}],
        }]},
    }
    result = analyze_simuhome_episode(episode)
    assert result.temporal is True
    assert result.td_action_coverage == 1.0
    assert result.td_action_ready is False
