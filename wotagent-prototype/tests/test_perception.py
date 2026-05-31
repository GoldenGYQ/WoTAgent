"""Tests for the perception module.

Note: ``DeviceStateStore`` is owned by ``wot-device-simulator``. Tests for it
live in that project.  Here we test only rule models and the perception engine
with mocked MCP calls.
"""
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
# PerceptionEngine (with mocked MCP)
# ======================================================================


def _mock_states(**overrides) -> dict:
    """Return a realistic set of device states for testing."""
    states = {
        "light-001": {"on": False, "brightness": 0},
        "ac-001": {"on": False, "currentTemperature": 25.0, "targetTemp": 24.0, "mode": "cool"},
        "humidifier-001": {"on": False, "currentHumidity": 50.0},
    }
    states.update(overrides)
    return states


class _MockMCPClient:
    """Mock MCP client that returns predefined states."""

    def __init__(self, states: dict | None = None):
        self._states = states or _mock_states()

    async def call_tool(self, tool_name: str, params: dict) -> dict:
        import json

        if tool_name == "query_all_states":
            return {"text": json.dumps(self._states)}
        if tool_name == "drift":
            return {"text": json.dumps({"success": True})}
        if tool_name == "query_state":
            device_id = params.get("device_id", "")
            state = self._states.get(device_id, {})
            return {"text": json.dumps({"device_id": device_id, "state": state})}
        return {"text": "{}"}


class TestPerceptionEngine:
    def test_poll_once_no_trigger(self):
        """Should not trigger rules at mild temperatures."""

        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            engine._fetch_all_states = lambda: _MockMCPClient(
                _mock_states(
                    ac_001={"on": False, "currentTemperature": 25.0, "targetTemp": 24.0, "mode": "cool"},
                    humidifier_001={"on": False, "currentHumidity": 50.0},
                )
            ).call_tool("query_all_states", {})
            engine._trigger_drift = lambda: _MockMCPClient().call_tool("drift", {})

            triggered = await engine.poll_once()
            assert triggered == []

        asyncio.run(run())

    def test_poll_once_trigger_high_temp(self):
        """Trigger high_temperature rule when temp > 32°C."""

        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            engine._fetch_all_states = lambda: _MockMCPClient(
                _mock_states(
                    ac_001={"on": False, "currentTemperature": 36.0, "targetTemp": 24.0, "mode": "cool"},
                )
            ).call_tool("query_all_states", {})
            engine._trigger_drift = lambda: _MockMCPClient().call_tool("drift", {})

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
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            engine._fetch_all_states = lambda: _MockMCPClient(
                _mock_states(
                    ac_001={"on": False, "currentTemperature": 36.0, "targetTemp": 24.0, "mode": "cool"},
                )
            ).call_tool("query_all_states", {})
            engine._trigger_drift = lambda: _MockMCPClient().call_tool("drift", {})

            for r in engine.rules:
                if r.name == "high_temperature":
                    r.last_triggered = time.time()
                    r.cooldown_seconds = 3600

            triggered = await engine.poll_once()
            names = [p.get("triggered_by") for p in triggered]
            assert "high_temperature" not in names

        asyncio.run(run())

    def test_get_context(self):
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            engine._fetch_all_states = lambda: _MockMCPClient().call_tool("query_all_states", {})

            ctx = await engine.get_context()
            assert ctx
            assert "ac-001" in ctx or "Current environment" in ctx

        asyncio.run(run())

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

    def test_get_all_states_returns_cached_on_mcp_fail(self):
        """get_all_states should return cached data when MCP is unavailable."""
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            # Set some cached states
            engine._cached_states = {
                "light-001": {"on": True, "brightness": 50},
            }
            # Make MCP fail
            async def _failing_fetch():
                raise ConnectionError("MCP unavailable")
            engine._fetch_all_states = _failing_fetch

            states = await engine.get_all_states()
            assert states == engine._cached_states

        asyncio.run(run())


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
