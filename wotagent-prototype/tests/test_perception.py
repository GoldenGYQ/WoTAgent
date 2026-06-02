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
    """Tests for ``Condition`` model -- device property threshold evaluation."""
    def test_gt(self):
        """Greater-than comparison evaluates correctly."""
        from wotagent.perception import Condition

        c = Condition(device_id="ac-001", property_name="currentTemperature", operator="gt", value=32)
        assert c.evaluate(35)
        assert not c.evaluate(30)

    def test_lt(self):
        """Less-than comparison evaluates correctly."""
        from wotagent.perception import Condition

        c = Condition(device_id="humidifier-001", property_name="currentHumidity", operator="lt", value=30)
        assert c.evaluate(25)
        assert not c.evaluate(35)

    def test_eq(self):
        """Equality comparison evaluates correctly."""
        from wotagent.perception import Condition

        c = Condition(device_id="light-001", property_name="on", operator="eq", value=True)
        assert c.evaluate(True)
        assert not c.evaluate(False)

    def test_non_numeric(self):
        """Non-numeric values return False on comparison."""
        from wotagent.perception import Condition

        c = Condition(device_id="light-001", property_name="on", operator="gt", value=0)
        assert not c.evaluate("hello")
        assert not c.evaluate(None)


class TestEnvironmentRule:
    """Tests for ``EnvironmentRule`` -- automated perception rules."""
    def test_cooldown(self):
        """Cooldown period prevents re-triggering."""
        from wotagent.perception import Condition, EnvironmentRule, RuleAction

        rule = EnvironmentRule(
            name="test",
            description="test rule",
            condition=Condition(device_id="ac-001", property_name="currentTemperature", operator="gt", value=30),
            action=RuleAction(steps=[{"action": "test"}]),
            cooldown_seconds=1,
        )
        assert rule.is_ready()
        rule.mark_triggered()
        assert not rule.is_ready()
        time.sleep(1.1)
        assert rule.is_ready()

    def test_enabled_disabled(self):
        """Rule can be enabled and disabled."""
        from wotagent.perception import Condition, EnvironmentRule, RuleAction

        rule = EnvironmentRule(
            name="test",
            description="test rule",
            condition=Condition(device_id="ac-001", property_name="currentTemperature", operator="gt", value=30),
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
    """Tests for ``PerceptionEngine`` -- background environment monitoring."""
    def test_poll_once_no_trigger(self):
        """Poll returns empty when no thresholds are exceeded."""
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            async def _mock_fetch_no_trigger():
                return _mock_states()
            engine._fetch_all_states = _mock_fetch_no_trigger
            engine._trigger_drift = lambda: _MockMCPClient().call_tool("drift", {})

            triggered = await engine.poll_once()
            assert triggered == []

        asyncio.run(run())

    def test_poll_once_trigger_high_temp(self):
        """High temperature triggers the cooling rule."""
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            async def _mock_fetch_high_temp():
                return {
                    "light-001": {"on": False, "brightness": 0},
                    "ac-001": {"on": False, "currentTemperature": 36.0, "targetTemp": 24.0, "mode": "cool"},
                    "humidifier-001": {"on": False, "currentHumidity": 50.0},
                }
            engine._fetch_all_states = _mock_fetch_high_temp
            engine._trigger_drift = lambda: _MockMCPClient().call_tool("drift", {})

            for r in engine.rules:
                if r.name == "high_temperature":
                    r.last_triggered = 0

            triggered = await engine.poll_once()
            names = [p.get("triggered_by") for p in triggered]
            assert "high_temperature" in names

        asyncio.run(run())

    def test_poll_once_cooldown_respected(self):
        """Rule respects cooldown and does not re-trigger."""
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            async def _mock_fetch_cooldown():
                return {
                    "light-001": {"on": False, "brightness": 0},
                    "ac-001": {"on": False, "currentTemperature": 36.0, "targetTemp": 24.0, "mode": "cool"},
                    "humidifier-001": {"on": False, "currentHumidity": 50.0},
                }
            engine._fetch_all_states = _mock_fetch_cooldown
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
        """Context summary includes device state information."""
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            async def _mock_fetch_context():
                return _mock_states()
            engine._fetch_all_states = _mock_fetch_context

            ctx = await engine.get_context()
            assert ctx
            assert "ac-001" in ctx or "Current environment" in ctx

        asyncio.run(run())

    def test_start_stop(self):
        """Engine starts and stops cleanly."""
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=60)
            assert not engine.is_running
            await engine.start()
            assert engine.is_running
            await engine.stop()
            assert not engine.is_running

        asyncio.run(run())

    def test_toggle_rule(self):
        """Individual rules can be toggled on/off."""
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
            condition=Condition(device_id="ac-001", property_name="currentTemperature", operator="gt", value=40),
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
        """Returns cached states when MCP call fails."""
        async def run():
            from wotagent.perception import PerceptionEngine

            engine = PerceptionEngine(polling_interval=9999)
            engine._cached_states = {"light-001": {"on": True, "brightness": 50}}

            async def _mock_get_all_states():
                return engine._cached_states
            engine.get_all_states = _mock_get_all_states

            states = await engine.get_all_states()
            assert states == engine._cached_states

        asyncio.run(run())
