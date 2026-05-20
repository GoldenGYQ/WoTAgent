"""In-memory event bus with pub/sub and cursor-based replay.

Thread-safe — intended for single-process use (FastAPI + Uvicorn).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any
from .schema import Event, EventType

logger = logging.getLogger(__name__)

EventCallback = Callable[[Event], Coroutine[Any, Any, None] | None]


class EventBus:
    """Publish/subscribe event bus.

    - ``emit(event)`` — deliver event to all matching subscribers
    - ``subscribe(type, callback)`` — register a consumer
    - ``unsubscribe(type, callback)`` — remove a consumer
    - ``history(start_cursor=0)`` — replay buffered events
    - ``clear()`` — drop buffered events
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._max_history = max_history
        self._subscribers: dict[EventType | None, list[EventCallback]] = {}
        self._history: list[Event] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def emit(self, event: Event) -> None:
        """Publish an event to all matching subscribers."""
        async with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history.pop(0)

            subscribers = list(self._subscribers.get(None, []))  # catch-all
            subscribers.extend(self._subscribers.get(event.type, []))

        for cb in subscribers:
            try:
                result = cb(event)
                if result is not None:
                    await result
            except Exception:
                logger.exception("EventBus subscriber error for %s", event.type)

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    def subscribe(
        self,
        callback: EventCallback,
        event_type: EventType | None = None,
    ) -> Callable[[], None]:
        """Register a subscriber.

        Args:
            callback: async fn(event) or sync fn(event).
            event_type: None means *all* events.

        Returns:
            A zero-arg callable that unsubscribes this callback.
        """
        self._subscribers.setdefault(event_type, []).append(callback)
        return lambda: self._unsubscribe(event_type, callback)

    def _unsubscribe(self, event_type: EventType | None, callback: EventCallback) -> None:
        subs = self._subscribers.get(event_type, [])
        if callback in subs:
            subs.remove(callback)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def history(self, start_cursor: int = 0) -> list[Event]:
        """Return events from ``start_cursor`` onward."""
        return list(self._history[start_cursor:])

    def cursor(self) -> int:
        """Current write position (length of history)."""
        return len(self._history)

    # ------------------------------------------------------------------
    # Admin
    # ------------------------------------------------------------------

    def clear(self) -> None:
        self._history.clear()

    @property
    def subscriber_count(self) -> int:
        subs = 0
        for v in self._subscribers.values():
            subs += len(v)
        return subs


# Singleton is fine for single-process mode.
_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
