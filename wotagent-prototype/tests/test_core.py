"""Tests for the new WoTAgent architecture."""

from __future__ import annotations

import pytest


# ======================================================================
# Event system
# ======================================================================

class TestEventSchema:
    def test_event_creation(self):
        from wotagent.events import Event
        evt = Event(
            source="wotagent/test",
            type="wot.system.log",
            data={"msg": "hello"},
            session_id="sess_001",
        )
        assert evt.specversion == "1.0"
        assert evt.id
        assert evt.source == "wotagent/test"
        assert evt.type == "wot.system.log"
        assert evt.data == {"msg": "hello"}
        assert evt.session_id == "sess_001"

    def test_event_types(self):
        from wotagent.events import Event, EventType
        types: list[EventType] = [
            "wot.session.started",
            "wot.agent.action.started",
            "wot.agent.action.completed",
            "wot.agent.response",
            "wot.device.state_changed",
        ]
        for t in types:
            evt = Event(source="test", type=t)
            assert evt.type == t


class TestEventBus:
    def test_emit_and_subscribe(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus(max_history=10)
            received = []

            async def collector(evt):
                received.append(evt)

            bus.subscribe(collector, event_type="wot.system.log")
            await bus.emit(Event(source="test", type="wot.system.log", data={"i": 1}))
            await bus.emit(Event(source="test", type="wot.system.log", data={"i": 2}))
            return received

        received = asyncio.run(run())
        assert len(received) == 2
        assert received[0].data["i"] == 1
        assert received[1].data["i"] == 2

    def test_history_replay(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus(max_history=5)
            for i in range(3):
                await bus.emit(Event(source="test", type="wot.system.log", data={"i": i}))
            return bus

        bus = asyncio.run(run())
        history = bus.history()
        assert len(history) == 3

        # Replay from cursor 1
        replay = bus.history(start_cursor=1)
        assert len(replay) == 2
        assert replay[0].data["i"] == 1

    def test_max_history(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus(max_history=3)
            for i in range(5):
                await bus.emit(Event(source="test", type="wot.system.log", data={"i": i}))
            return bus

        bus = asyncio.run(run())
        assert len(bus.history()) == 3
        assert bus.history()[0].data["i"] == 2  # oldest kept

    def test_cursor(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus()
            assert bus.cursor() == 0
            await bus.emit(Event(source="test", type="wot.system.log"))
            assert bus.cursor() == 1

    def test_subscriber_count(self):
        from wotagent.events import EventBus, Event

        bus = EventBus()
        assert bus.subscriber_count == 0

        def cb(evt):
            pass

        bus.subscribe(cb)
        assert bus.subscriber_count == 1

    def test_unsubscribe(self):
        from wotagent.events import EventBus, Event

        bus = EventBus()
        calls = []

        def cb(evt):
            calls.append(evt)

        unsub = bus.subscribe(cb)
        unsub()
        assert bus.subscriber_count == 0


# ======================================================================
# RBAC
# ======================================================================

class TestRBAC:
    def test_role_hierarchy(self):
        from wotagent.auth import Role, get_access_control

        ac = get_access_control()
        # Admin can do anything
        assert ac.check(Role.ADMIN, "device.control", "execute")
        assert ac.check(Role.ADMIN, "session.list", "read")
        # Viewer can read but not control
        assert ac.check(Role.VIEWER, "device.read", "read")
        assert not ac.check(Role.VIEWER, "device.control", "execute")
        # Operator can control
        assert ac.check(Role.OPERATOR, "device.control", "execute")

    def test_role_enum(self):
        from wotagent.auth import Role

        assert Role.ADMIN.value == "admin"
        assert Role.OPERATOR.value == "operator"
        assert Role.VIEWER.value == "viewer"

    def test_wildcard_match(self):
        from wotagent.auth import Role, get_access_control

        ac = get_access_control()
        # Admin has wildcard permissions
        assert ac.check(Role.ADMIN, "device.anything.here", "any_action")
        assert ac.check(Role.ADMIN, "nonexistent.resource", "read")


# ======================================================================
# WoT / Thing Description
# ======================================================================

class TestWoT:
    def test_find_devices(self):
        from wotagent.wot import find_devices

        devices = find_devices()
        assert isinstance(devices, list)
        # At least the sample TDs should be found
        assert len(devices) >= 3

    def test_find_devices_by_capability(self):
        from wotagent.wot import find_devices

        luminance = find_devices(capability="luminance")
        for d in luminance:
            caps = [c.lower() for c in d.capabilities]
            assert "luminance" in caps

    def test_find_devices_by_location(self):
        from wotagent.wot import find_devices

        parlor = find_devices(location="parlor")
        assert len(parlor) > 0, "No devices found for parlor (should map to living_room)"
        for d in parlor:
            assert d.location.lower() == "living_room"

    def test_get_device_by_id(self):
        from wotagent.wot import get_device_by_id

        device = get_device_by_id("light-001")
        if device:
            assert device.title


# ======================================================================
# Memory
# ======================================================================

class TestMemory:
    def test_add_and_retrieve(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_1", window_size=5)
        mem.add_user_message("hello")
        mem.add_ai_message("hi there")

        assert mem.message_count == 2
        assert len(mem.messages) == 2
        assert mem.messages[0].content == "hello"
        assert mem.messages[1].content == "hi there"

    def test_window_trimming(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_2", window_size=2)
        for i in range(10):
            mem.add_user_message(f"q{i}")
            mem.add_ai_message(f"a{i}")

        # Should keep system (none) + last 2 exchanges = 4 messages
        assert mem.message_count <= 5

    def test_clear(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_3")
        mem.add_user_message("hello")
        mem.clear()
        assert mem.message_count == 0

    def test_save_and_load(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_test_save_load")
        mem.add_user_message("你好")
        mem.add_ai_message("你好！有什么可以帮你的吗？")
        mem.save()

        # Create a new instance and load from disk
        mem2 = ConversationMemory("sess_test_save_load")
        mem2.load()
        assert mem2.message_count == 2
        assert mem2.messages[0].content == "你好"
        assert mem2.messages[1].content == "你好！有什么可以帮你的吗？"

        # Cleanup
        import os
        os.remove(mem2._data_path())

    def test_load_nonexistent(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_does_not_exist")
        assert mem.message_count == 0  # no crash

    def test_truncate(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_test_truncate")
        for i in range(5):
            mem.add_user_message(f"q{i}")
            mem.add_ai_message(f"a{i}")
        assert mem.message_count == 10

        # Truncate to index 3 (keep first 4 messages: q0, a0, q1, a1)
        mem.truncate(3)
        assert mem.message_count == 4
        assert mem.messages[0].content == "q0"
        assert mem.messages[3].content == "a1"

        # Cleanup
        import os
        os.remove(mem._data_path())

    def test_truncate_negative_index(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_test_truncate_neg")
        mem.add_user_message("q0")
        mem.add_ai_message("a0")
        mem.add_user_message("q1")
        mem.truncate(-1)  # keep all
        assert mem.message_count == 3

        import os
        os.remove(mem._data_path())


# ======================================================================
# Tools
# ======================================================================

class TestTools:
    def test_get_all_tools(self):
        from wotagent.tools import get_all_tools

        tools = get_all_tools()
        assert len(tools) >= 6
        names = [t.name for t in tools]
        assert "list_devices" in names
        assert "control_device" in names
        assert "query_device" in names
        assert "rag_retrieve" in names
        assert "get_system_info" in names
        assert "list_files" in names

    def test_tool_metadata(self):
        from wotagent.tools import get_all_tools

        for t in get_all_tools():
            assert t.name
            assert t.description

    def test_list_devices_tool(self):
        from wotagent.tools import get_all_tools

        tools = {t.name: t for t in get_all_tools()}
        list_tool = tools["list_devices"]
        result = list_tool.func()
        assert isinstance(result, list)


# ======================================================================
# Agent creation
# ======================================================================

class TestAgent:
    def test_create_agent(self):
        from wotagent.core import create_wot_agent

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        assert agent is not None
        assert hasattr(agent, "ainvoke")
        assert hasattr(agent, "astream_events")

    def test_create_pipeline(self):
        from wotagent.core import create_wot_pipeline, AgentPipeline

        pipeline = create_wot_pipeline(enable_thinking=False, temperature=0)
        assert isinstance(pipeline, AgentPipeline)
        assert hasattr(pipeline.planner, "ainvoke")
        assert hasattr(pipeline.executor, "ainvoke")
        assert hasattr(pipeline.observer, "ainvoke")
        assert hasattr(pipeline.responder, "ainvoke")


# ======================================================================
# Session management
# ======================================================================

class TestSession:
    def _fresh_manager(self):
        from wotagent.core.session import SessionManager
        return SessionManager(timeout_minutes=60)

    def test_create_and_get(self):
        from wotagent.core import create_wot_agent

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        mgr = self._fresh_manager()

        session = mgr.create(agent, user_role="operator")
        assert session.session_id
        assert session.user_role == "operator"

        retrieved = mgr.get(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_or_create(self):
        from wotagent.core import create_wot_agent

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        mgr = self._fresh_manager()

        s1 = mgr.get_or_create(agent, user_role="viewer")
        s2 = mgr.get_or_create(agent, session_id=s1.session_id, user_role="viewer")
        assert s1.session_id == s2.session_id

    def test_session_expiry(self):
        import time
        from wotagent.core import create_wot_agent
        from wotagent.core.session import SessionManager

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        mgr = SessionManager(timeout_minutes=0)  # immediate expiry
        session = mgr.create(agent)
        time.sleep(0.01)  # ensure clock ticks past 0
        assert mgr.get(session.session_id) is None

    def test_list_active(self):
        from wotagent.core import create_wot_agent

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        mgr = self._fresh_manager()
        mgr.create(agent)
        mgr.create(agent)
        assert len(mgr.list_active()) == 2


# ======================================================================
# Event Logging
# ======================================================================

class TestEventLogging:
    def test_event_logger_writes_to_log_file(self, tmp_path):
        """install_event_logger() should write bus events to the log file."""
        import asyncio
        import logging
        from wotagent.events import Event, get_bus
        from wotagent.logging import configure_logging, install_event_logger

        log_file = tmp_path / "test_events.log"
        configure_logging(mode="file", log_file=str(log_file))

        # Reset event bus to avoid cross-test pollution
        from wotagent.events.bus import EventBus
        import wotagent.events.bus as bus_module
        bus_module._bus = EventBus()
        bus = get_bus()

        install_event_logger()

        async def emit():
            await bus.emit(Event(
                source="wotagent/test",
                type="wot.agent.thought",
                data={"content": "用户用中文打招呼"},
            ))
            await bus.emit(Event(
                source="wotagent/tools/iot",
                type="wot.agent.action.started",
                data={"device": "light-001", "action": "turnOn"},
            ))

        asyncio.run(emit())

        # Flush logging handlers
        for h in logging.getLogger().handlers:
            h.flush()

        content = log_file.read_text(encoding="utf-8")
        assert "wot.agent.thought" in content
        assert "用户用中文打招呼" in content
        assert "wot.agent.action.started" in content
        assert "light-001" in content
