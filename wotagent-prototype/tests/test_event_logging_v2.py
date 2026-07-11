"""Regression tests for event-backed logging loop ownership."""

from __future__ import annotations

import asyncio
import logging

from wotagent.events import EventBus
from wotagent.logging.logger import EventBusHandler


def record() -> logging.LogRecord:
    return logging.LogRecord(
        name="wotagent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )


def test_sync_logging_does_not_create_a_dormant_coroutine() -> None:
    handler = EventBusHandler()
    handler._bus = EventBus()

    handler.emit(record())

    assert handler._bus.history() == []


def test_async_logging_is_forwarded_on_the_running_loop() -> None:
    async def run() -> list:
        handler = EventBusHandler()
        handler._bus = EventBus()
        handler.emit(record())
        await asyncio.sleep(0)
        return handler._bus.history()

    history = asyncio.run(run())

    assert len(history) == 1
    assert history[0].type == "wot.system.log"
    assert history[0].data["message"] == "test message"
