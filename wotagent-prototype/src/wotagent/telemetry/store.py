"""In-process trace store used by local development and deterministic tests."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class TraceEvent(BaseModel):
    event_id: str
    sequence: int
    timestamp: datetime = Field(default_factory=utc_now)
    trace_id: str
    task_id: str
    session_id: str = ""
    type: str
    node: str = ""
    iteration: int = 0
    duration_ms: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    raw_data: Any | None = Field(default=None, exclude=True)


class UsageSummary(BaseModel):
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    estimated_cost: float = 0.0
    currency: str = "CNY"
    pricing_available: bool = False
    model_latency_ms: float = 0.0
    ttft_ms_total: float = 0.0
    ttft_measurements: int = 0


class TraceRecord(BaseModel):
    trace_id: str
    task_id: str
    session_id: str
    request_id: str
    status: str = "received"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    loop_count: int = 0
    recovery_count: int = 0
    usage: UsageSummary = Field(default_factory=UsageSummary)
    result: dict[str, Any] | None = None


class InMemoryTraceStore:
    """Ordered trace storage with per-task notification for SSE consumers."""

    def __init__(self) -> None:
        self._records: dict[str, TraceRecord] = {}
        self._events: dict[str, list[TraceEvent]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._listeners: dict[
            str,
            dict[str, Callable[[TraceEvent], Awaitable[None] | None]],
        ] = {}
        self._lock = asyncio.Lock()

    def subscribe(
        self,
        task_id: str,
        listener: Callable[[TraceEvent], Awaitable[None] | None],
    ) -> Callable[[], None]:
        token = uuid4().hex
        self._listeners.setdefault(task_id, {})[token] = listener

        def unsubscribe() -> None:
            listeners = self._listeners.get(task_id)
            if listeners is None:
                return
            listeners.pop(token, None)
            if not listeners:
                self._listeners.pop(task_id, None)

        return unsubscribe

    async def create(
        self,
        *,
        task_id: str,
        session_id: str,
        request_id: str,
    ) -> TraceRecord:
        async with self._lock:
            record = TraceRecord(
                trace_id=task_id,
                task_id=task_id,
                session_id=session_id,
                request_id=request_id,
            )
            self._records[task_id] = record
            self._events[task_id] = []
            self._conditions[task_id] = asyncio.Condition()
            return record.model_copy(deep=True)

    async def record(
        self,
        *,
        task_id: str,
        type: str,
        node: str = "",
        data: dict[str, Any] | None = None,
        raw_data: Any | None = None,
        iteration: int = 0,
        duration_ms: float | None = None,
    ) -> TraceEvent:
        async with self._lock:
            record = self._records[task_id]
            events = self._events[task_id]
            event = TraceEvent(
                event_id=f"{task_id}:{len(events) + 1}",
                sequence=len(events) + 1,
                trace_id=record.trace_id,
                task_id=task_id,
                session_id=record.session_id,
                type=type,
                node=node,
                iteration=iteration,
                duration_ms=duration_ms,
                data=data or {},
                raw_data=raw_data,
            )
            events.append(event)
            record.updated_at = utc_now()
            if type == "model.request.completed":
                record.usage.model_calls += 1
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                    "cache_hit_tokens",
                    "cache_miss_tokens",
                ):
                    setattr(record.usage, key, getattr(record.usage, key) + int(event.data.get(key, 0)))
                record.usage.estimated_cost += float(event.data.get("estimated_cost", 0.0))
                record.usage.currency = str(event.data.get("currency", record.usage.currency))
                record.usage.pricing_available = (
                    record.usage.pricing_available or bool(event.data.get("pricing_available", False))
                )
                record.usage.model_latency_ms += float(duration_ms or 0.0)
                if event.data.get("ttft_ms") is not None:
                    record.usage.ttft_ms_total += float(event.data["ttft_ms"])
                    record.usage.ttft_measurements += 1
            elif type in {"agent.tool.completed", "agent.resource.completed"}:
                record.usage.tool_calls += 1
            elif type == "agent.loop.completed":
                record.loop_count = max(record.loop_count, iteration)
            elif type == "agent.recovery.completed":
                record.recovery_count += 1
            condition = self._conditions[task_id]
            listeners = list(self._listeners.get(task_id, {}).values())
        async with condition:
            condition.notify_all()
        projected = event.model_copy(deep=True)
        for listener in listeners:
            try:
                result = listener(projected.model_copy(deep=True))
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Observability projection must not break device execution.
                continue
        return projected

    async def finish(self, task_id: str, *, status: str, result: dict[str, Any]) -> None:
        async with self._lock:
            record = self._records[task_id]
            record.status = status
            record.result = result
            record.updated_at = utc_now()
            condition = self._conditions[task_id]
        async with condition:
            condition.notify_all()

    async def set_status(self, task_id: str, status: str) -> None:
        async with self._lock:
            record = self._records[task_id]
            record.status = status
            record.updated_at = utc_now()
            condition = self._conditions[task_id]
        async with condition:
            condition.notify_all()

    async def get(self, task_id: str) -> TraceRecord | None:
        async with self._lock:
            record = self._records.get(task_id)
            return record.model_copy(deep=True) if record else None

    async def events(self, task_id: str, *, after: int = 0) -> list[TraceEvent]:
        async with self._lock:
            return [event.model_copy(deep=True) for event in self._events.get(task_id, []) if event.sequence > after]

    async def wait_for_events(self, task_id: str, *, after: int, timeout: float = 15.0) -> list[TraceEvent]:
        current = await self.events(task_id, after=after)
        if current:
            return current
        condition = self._conditions.get(task_id)
        if condition is None:
            return []
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except TimeoutError:
            return []
        return await self.events(task_id, after=after)
