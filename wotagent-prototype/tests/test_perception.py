"""Tests for the perception module."""
from __future__ import annotations

import asyncio
import time


# ======================================================================
# Condition & Rule model
# ======================================================================


class TestCondition:
    def test_gt(self):
        from wotagent.perception import Condition

        c = Condition("ac-001", "currentTemperature", "gt", 32)
        assert c.evaluate(35)
        assert not c.evaluate(30)

    def test_lt(self):
        from wotagent.perception import Condition

        c = Condition("humidifier-001", "currentHumidity", "lt", 30)
        assert c.evaluate(25)
        assert not c.evaluate(35)

    def test_eq(self):
        from wotagent.perception import Condition

        c = Condition("light-001", "on", "eq", True)
        assert c.evaluate(True)
        assert not c.evaluate(False)

    def test_non_numeric(self):
        from wotagent.perception import Condition

        c = Condition("light-001", "on", "gt", 0)
        assert not c.evaluate("hello")
        assert not c.evaluate(None)


class TestEnvironmentRule:
    def test_cooldown(self):
        from wotagent.perception import Condition, EnvironmentRule, RuleAction

        rule = EnvironmentRule(
            name="test",
            description="test rule",
            condition=Condition("ac-001", "currentTemperature", "gt", 30),
            action=RuleAction(steps=[{"action": "test"}]),
            cooldown_seconds=1,
        )
        assert rule.is_ready()
        rule.mark_triggered()
        assert not rule.is_ready()
        time.sleep(1.1)
        assert rule.is_ready()

    def test_enabled_disabled(self):
        from wotagent.perception import Condition, EnvironmentRule, RuleAction

        rule = EnvironmentRule(
            name="test",
            description="test rule",
            condition=Condition("ac-001", "currentTemperature", "gt", 30),
            action=RuleAction(steps=[]),
            enabled=False,
        )
        assert not rule.enabled
        rule.enabled = True
        assert rule.enabled


# ======================================================================
# DeviceStateStore
# ======================================================================


class TestDeviceStateStore:
    def test_initialise(self):
        from wotagent.perception import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        state = DeviceStateStore.get_all()
        assert len(state) >= 3
        assert "light-001" in state
        assert "ac-001" in state
        assert "humidifier-001" in state

    def test_get_property(self):
        from wotagent.perception import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        temp = DeviceStateStore.get_property("ac-001", "currentTemperature")
        assert isinstance(temp, float)

    def test_set_property(self):
        from wotagent.perception import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        DeviceStateStore.set_property("light-001", "on", True)
        assert DeviceStateStore.get_property("light-001", "on") is True

    def test_drift_changes_values(self):
        from wotagent.perception import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        before = DeviceStateStore.get_property("ac-001", "currentTemperature")
        for _ in range(10):
            DeviceStateStore.drift()
        after = DeviceStateStore.get_property("ac-001", "currentTemperature")
        assert before != after or abs(before - after) > 0.01

    def test_format_summary(self):
        from wotagent.perception import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        summary = DeviceStateStore.format_summary()
        assert summary
        assert "ac-001" in summary


# ======================================================================
# PerceptionEngine
# ======================================================================


class TestPerceptionEngine:
    def test_poll_once_no_trigger(self):
        """Should not trigger rules at mild temperatures."""

        async def run():
            from wotagent.perception import DeviceStateStore, PerceptionEngine

            DeviceStateStore.reset()
            DeviceStateStore.initialize()
            DeviceStateStore.set_property("ac-001", "currentTemperature", 25.0)
            DeviceStateStore.set_property("humidifier-001", "currentHumidity", 50.0)

            engine = PerceptionEngine(polling_interval=9999)
            triggered = await engine.poll_once()
            assert triggered == []

        asyncio.run(run())

    def test_poll_once_trigger_high_temp(self):
        """Trigger high_temperature rule when temp > 32°C."""

        async def run():
            from wotagent.perception import DeviceStateStore, PerceptionEngine

            DeviceStateStore.reset()
            DeviceStateStore.initialize()
            DeviceStateStore.set_property("ac-001", "currentTemperature", 36.0)

            engine = PerceptionEngine(polling_interval=9999)
            for r in engine.rules:
                if r.name == "high_temperature":
                    r.last_triggered = 0

            triggered = await engine.poll_once()
            names = [p.get("triggered_by") for p in triggered]
            assert "high_temperature" in names

        asyncio.run(run())

    def test_poll_once_cooldown_respected(self):
        """Rule should NOT re-trigger within cooldown."""

        async def run():
            from wotagent.perception import DeviceStateStore, PerceptionEngine

            DeviceStateStore.reset()
            DeviceStateStore.initialize()
            DeviceStateStore.set_property("ac-001", "currentTemperature", 36.0)

            engine = PerceptionEngine(polling_interval=9999)
            for r in engine.rules:
                if r.name == "high_temperature":
                    r.last_triggered = time.time()
                    r.cooldown_seconds = 3600

            triggered = await engine.poll_once()
            names = [p.get("triggered_by") for p in triggered]
            assert "high_temperature" not in names

        asyncio.run(run())

    def test_get_context(self):
        from wotagent.perception import DeviceStateStore, PerceptionEngine

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        engine = PerceptionEngine(polling_interval=9999)
        ctx = engine.get_context()
        assert ctx
        assert "ac-001" in ctx or "Current environment" in ctx

    def test_start_stop(self):
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=60)
            assert not engine.is_running
            engine.start()
            assert engine.is_running
            await engine.stop()
            assert not engine.is_running

        asyncio.run(run())

    def test_toggle_rule(self):
        from wotagent.perception import (
            Condition,
            EnvironmentRule,
            PerceptionEngine,
            RuleAction,
        )

        engine = PerceptionEngine(polling_interval=9999)
        rule = EnvironmentRule(
            name="test_toggle",
            description="test",
            condition=Condition("ac-001", "currentTemperature", "gt", 40),
            action=RuleAction(steps=[]),
        )
        engine.rules.append(rule)

        assert engine.get_rule("test_toggle") is not None
        assert engine.get_rule("nonexistent") is None

        engine.toggle_rule("test_toggle", enabled=False)
        assert not rule.enabled

        engine.toggle_rule("test_toggle")
        assert rule.enabled


# ======================================================================
# Default rules
# ======================================================================


class TestDefaultRules:
    def test_default_rules_count(self):
        from wotagent.perception import default_rules

        rules = default_rules()
        assert len(rules) >= 2

    def test_default_rules_have_names(self):
        from wotagent.perception import default_rules

        for r in default_rules():
            assert r.name
            assert r.description
            assert r.action.steps
