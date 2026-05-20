"""Tests for Condition and EnvironmentRule models."""
from __future__ import annotations

import time


class TestCondition:
    def test_gt(self):
        from wot_device_simulator.rules import Condition

        c = Condition("ac-001", "currentTemperature", "gt", 32)
        assert c.evaluate(35)
        assert not c.evaluate(30)
        assert not c.evaluate(32)

    def test_lt(self):
        from wot_device_simulator.rules import Condition

        c = Condition("humidifier-001", "currentHumidity", "lt", 30)
        assert c.evaluate(25)
        assert not c.evaluate(35)
        assert not c.evaluate(30)

    def test_gte(self):
        from wot_device_simulator.rules import Condition

        c = Condition("ac-001", "currentTemperature", "gte", 32)
        assert c.evaluate(35)
        assert c.evaluate(32)
        assert not c.evaluate(30)

    def test_lte(self):
        from wot_device_simulator.rules import Condition

        c = Condition("ac-001", "currentTemperature", "lte", 25)
        assert c.evaluate(20)
        assert c.evaluate(25)
        assert not c.evaluate(30)

    def test_eq(self):
        from wot_device_simulator.rules import Condition

        c = Condition("light-001", "on", "eq", True)
        assert c.evaluate(True)
        assert not c.evaluate(False)

    def test_ne(self):
        from wot_device_simulator.rules import Condition

        c = Condition("tv-001", "on", "ne", True)
        assert c.evaluate(False)
        assert not c.evaluate(True)

    def test_non_numeric(self):
        from wot_device_simulator.rules import Condition

        c = Condition("light-001", "on", "gt", 0)
        assert not c.evaluate("hello")
        assert not c.evaluate(None)

    def test_unknown_operator(self):
        from wot_device_simulator.rules import Condition

        c = Condition("ac-001", "temp", "unknown_op", 25)
        assert not c.evaluate(30)


class TestEnvironmentRule:
    def test_create_and_ready(self):
        from wot_device_simulator.rules import Condition, EnvironmentRule, RuleAction

        rule = EnvironmentRule(
            name="test_rule",
            description="A test rule",
            condition=Condition("ac-001", "currentTemperature", "gt", 30),
            action=RuleAction(steps=[{"action": "control_device", "target": "ac-001"}]),
            cooldown_seconds=300,
        )
        assert rule.name == "test_rule"
        assert rule.description == "A test rule"
        assert rule.is_ready()
        assert rule.enabled is True

    def test_cooldown(self):
        from wot_device_simulator.rules import Condition, EnvironmentRule, RuleAction

        rule = EnvironmentRule(
            name="cooldown_test",
            description="cooldown test",
            condition=Condition("ac-001", "currentTemperature", "gt", 30),
            action=RuleAction(steps=[]),
            cooldown_seconds=1,
        )
        assert rule.is_ready()
        rule.mark_triggered()
        assert not rule.is_ready()
        time.sleep(1.1)
        assert rule.is_ready()

    def test_disabled(self):
        from wot_device_simulator.rules import Condition, EnvironmentRule, RuleAction

        rule = EnvironmentRule(
            name="disabled_test",
            description="disabled test",
            condition=Condition("ac-001", "currentTemperature", "gt", 30),
            action=RuleAction(steps=[]),
            enabled=False,
        )
        assert not rule.enabled
        rule.enabled = True
        assert rule.enabled

    def test_intent_default(self):
        from wot_device_simulator.rules import RuleAction

        a = RuleAction(steps=[{"action": "test"}])
        assert a.intent == "control"

    def test_default_rules(self):
        from wot_device_simulator.rules import default_rules

        rules = default_rules()
        assert len(rules) >= 2
        for r in rules:
            assert r.name
            assert r.description
            assert r.action.steps
