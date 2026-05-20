"""Tests for DeviceStateStore (SQLite) and SimulatorEngine."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest


# ======================================================================
# DeviceStateStore
# ======================================================================


class TestDeviceStateStore:
    def test_initialize_creates_tables(self):
        from wot_device_simulator.simulator import DeviceStateStore, _DB_PATH

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        assert _DB_PATH.exists()

        conn = sqlite3.connect(str(_DB_PATH))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r[0] for r in tables]
        assert "device_state" in names
        assert "device_info" in names
        conn.close()

    def test_initialize_seeds_all_devices(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        state = DeviceStateStore.get_all()
        assert len(state) >= 3
        assert "light-001" in state
        assert "ac-001" in state

    def test_get_returns_empty_for_unknown(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        assert DeviceStateStore.get("nonexistent") == {}

    def test_get_property(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        on = DeviceStateStore.get_property("light-001", "on")
        assert on is False

    def test_get_property_nonexistent(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        assert DeviceStateStore.get_property("light-001", "nonexistent_prop") is None

    def test_set_property(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        DeviceStateStore.set_property("light-001", "on", True)
        assert DeviceStateStore.get_property("light-001", "on") is True

    def test_set_property_creates_new_device(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        DeviceStateStore.set_property("custom-device", "test", 42)
        state = DeviceStateStore.get("custom-device")
        assert state.get("test") == 42

    def test_set_property_persists_to_db(self):
        from wot_device_simulator.simulator import DeviceStateStore, _DB_PATH

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        DeviceStateStore.set_property("light-001", "on", True)

        # Read directly from SQLite to verify persistence
        conn = sqlite3.connect(str(_DB_PATH))
        row = conn.execute(
            "SELECT state FROM device_state WHERE device_id = ?", ("light-001",)
        ).fetchone()
        props = json.loads(row[0])
        assert props["on"] is True
        conn.close()

    def test_drift_changes_temperature(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        before = DeviceStateStore.get_property("ac-001", "currentTemperature")
        for _ in range(10):
            DeviceStateStore.drift()
        after = DeviceStateStore.get_property("ac-001", "currentTemperature")
        assert abs(before - after) > 0.01

    def test_drift_cooling_pulls_temp_down(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        DeviceStateStore.set_property("ac-001", "on", True)
        DeviceStateStore.set_property("ac-001", "mode", "cool")
        DeviceStateStore.set_property("ac-001", "targetTemp", 20)

        before = DeviceStateStore.get_property("ac-001", "currentTemperature")
        for _ in range(20):
            DeviceStateStore.drift()
        after = DeviceStateStore.get_property("ac-001", "currentTemperature")

        # Temperature should trend downwards toward 20
        assert after < before

    def test_format_summary(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        summary = DeviceStateStore.format_summary()
        assert summary
        assert "ac-001" in summary
        assert "light-001" in summary

    def test_get_all_with_info_includes_location(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        devices = DeviceStateStore.get_all_with_info()
        assert len(devices) >= 3

        light = next(d for d in devices if d["device_id"] == "light-001")
        assert light["location"] == "living_room"
        assert "luminance" in light["capabilities"]
        assert "state" in light
        assert "on" in light["state"]

    def test_get_all_with_info_order(self):
        from wot_device_simulator.simulator import DeviceStateStore

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        devices = DeviceStateStore.get_all_with_info()
        ids = [d["device_id"] for d in devices]
        assert ids == sorted(ids)

    def test_reset_clears_all(self):
        from wot_device_simulator.simulator import DeviceStateStore, _DB_PATH, _get_db

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        DeviceStateStore.set_property("light-001", "on", True)
        DeviceStateStore.reset()

        # Raw DB should be empty after reset
        conn = _get_db()
        count = conn.execute("SELECT COUNT(*) FROM device_state").fetchone()[0]
        assert count == 0
        count = conn.execute("SELECT COUNT(*) FROM device_info").fetchone()[0]
        assert count == 0

        # After re-initialize, all TDs are re-seeded with fresh defaults
        DeviceStateStore.initialize()
        assert DeviceStateStore.get_property("light-001", "on") is False  # not True


# ======================================================================
# SimulatorEngine
# ======================================================================


class TestSimulatorEngine:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        from wot_device_simulator.simulator import SimulatorEngine

        engine = SimulatorEngine(polling_interval=60)
        assert not engine.is_running
        engine.start()
        assert engine.is_running
        await engine.stop()
        assert not engine.is_running

    @pytest.mark.asyncio
    async def test_control_device_turn_on(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        result = await SimulatorEngine().control_device("light-001", "turnOn")
        assert result["success"] is True
        assert DeviceStateStore.get_property("light-001", "on") is True

    @pytest.mark.asyncio
    async def test_control_device_turn_off(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        await SimulatorEngine().control_device("light-001", "turnOn")
        await SimulatorEngine().control_device("light-001", "turnOff")
        assert DeviceStateStore.get_property("light-001", "on") is False

    @pytest.mark.asyncio
    async def test_control_device_set_brightness(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        await SimulatorEngine().control_device("light-001", "setBrightness", {"brightness": 50})
        assert DeviceStateStore.get_property("light-001", "on") is True
        assert DeviceStateStore.get_property("light-001", "brightness") == 50

    @pytest.mark.asyncio
    async def test_control_device_set_temperature(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        # Initial temp from default
        initial_temp = DeviceStateStore.get_property("ac-001", "currentTemperature")

        await SimulatorEngine().control_device("ac-001", "setTemperature", {"temp": 26})
        assert DeviceStateStore.get_property("ac-001", "on") is True
        assert DeviceStateStore.get_property("ac-001", "targetTemp") == 26
        # Should have moved toward 26
        current_temp = DeviceStateStore.get_property("ac-001", "currentTemperature")
        assert abs(current_temp - 26) <= 2.1

    @pytest.mark.asyncio
    async def test_control_device_set_mode(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        await SimulatorEngine().control_device("ac-001", "setMode", {"mode": "cool"})
        assert DeviceStateStore.get_property("ac-001", "mode") == "cool"

    @pytest.mark.asyncio
    async def test_control_device_nonexistent(self):
        from wot_device_simulator.simulator import SimulatorEngine

        result = await SimulatorEngine().control_device("nonexistent-device", "turnOn")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_poll_once(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        DeviceStateStore.set_property("ac-001", "currentTemperature", 36.0)

        engine = SimulatorEngine(polling_interval=9999)
        for r in engine.rules:
            if r.name == "high_temperature":
                r.last_triggered = 0

        triggered = await engine.poll_once()
        names = [p.get("triggered_by") for p in triggered]
        assert "high_temperature" in names

    @pytest.mark.asyncio
    async def test_patch_state(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        result = await SimulatorEngine().patch_state("light-001", "on", True)
        assert result["success"] is True
        assert DeviceStateStore.get_property("light-001", "on") is True

    @pytest.mark.asyncio
    async def test_patch_state_nonexistent(self):
        from wot_device_simulator.simulator import SimulatorEngine

        result = await SimulatorEngine().patch_state("nonexistent", "on", True)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_apply_environment_temperature(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        result = await SimulatorEngine().apply_environment({"temperature": 30.0})
        assert result["success"] is True

        # AC temperatures should now be near 30
        ac_temp = DeviceStateStore.get_property("ac-001", "currentTemperature")
        assert abs(ac_temp - 30.0) < 1.0

    @pytest.mark.asyncio
    async def test_control_device_stats(self):
        from wot_device_simulator.simulator import SimulatorEngine

        engine = SimulatorEngine(polling_interval=9999)
        await engine.control_device("light-001", "turnOn")
        assert engine.stats["controls"] == 1

    @pytest.mark.asyncio
    async def test_inject_event(self):
        from wot_device_simulator.simulator import SimulatorEngine

        engine = SimulatorEngine(polling_interval=9999)
        result = await engine.inject_event("test.event", {"msg": "hello"})
        assert result["success"] is True
        events = engine.recent_events()
        assert len(events) >= 1

    def test_get_context(self):
        from wot_device_simulator.simulator import DeviceStateStore, SimulatorEngine

        DeviceStateStore.reset()
        DeviceStateStore.initialize()
        engine = SimulatorEngine(polling_interval=9999)
        ctx = engine.get_context()
        assert ctx
        assert "ac-001" in ctx or "Current" in ctx

    def test_recent_events_limit(self):
        from wot_device_simulator.simulator import SimulatorEngine

        engine = SimulatorEngine(polling_interval=9999)
        assert engine.recent_events(0) == []
        assert engine.recent_events(-1) == []

    def test_subscribe_unsubscribe(self):
        from wot_device_simulator.simulator import SimulatorEngine

        engine = SimulatorEngine(polling_interval=9999)
        q = engine.subscribe()
        engine.unsubscribe(q)
        # Should not crash unsubscribing twice
        engine.unsubscribe(q)
