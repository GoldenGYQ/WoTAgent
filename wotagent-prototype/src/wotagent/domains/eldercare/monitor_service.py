"""Independent device collection, persistence, rule evaluation, and event publishing."""

from __future__ import annotations

import asyncio
from typing import Any

from .collector import EldercareDeviceCollector
from .models import SensorReading
from .monitor import HealthMonitor
from .store import EldercareStore


class EldercareMonitorService:
    def __init__(self, store: EldercareStore, gateway: Any) -> None:
        self.store = store
        self.collector = EldercareDeviceCollector(store, gateway)
        self.monitor = HealthMonitor(store)

    async def ingest(self, reading: SensorReading) -> dict[str, Any]:
        self.store.add_reading(reading)
        events = self.monitor.evaluate_resident(
            reading.resident_id,
            now=reading.observed_at,
        )
        outbox = [self.store.enqueue_event(event) for event in events]
        return {
            "reading": reading.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
            "outbox": outbox,
        }

    async def collect_once(self) -> list[dict[str, Any]]:
        results = [await self.ingest(reading) for reading in await self.collector.collect_all()]
        self.store.heartbeat(
            "eldercare-monitor",
            details={"changed_readings": len(results)},
        )
        return results

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        interval_seconds: float = 2.0,
    ) -> None:
        while not stop_event.is_set():
            try:
                await self.collect_once()
            except Exception as exc:
                self.store.heartbeat(
                    "eldercare-monitor",
                    status="degraded",
                    details={"error": str(exc)[:500]},
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass
        self.store.heartbeat("eldercare-monitor", status="stopped")
