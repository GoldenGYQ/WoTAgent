"""Event-driven runtime joining sensor ingestion, monitoring, and Butler delegation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..telemetry import InMemoryTraceStore
from .actions import EldercareActionService
from .graph import build_eldercare_butler_graph
from .models import RiskEvent, SensorReading
from .monitor import HealthMonitor
from .store import EldercareStore


class EldercareRuntime:
    def __init__(
        self,
        store: EldercareStore,
        *,
        trace_store: InMemoryTraceStore | None = None,
        environment_gateway: Any | None = None,
    ) -> None:
        self.store = store
        self.trace_store = trace_store or InMemoryTraceStore()
        self.monitor = HealthMonitor(store)
        self.actions = EldercareActionService(store, environment_gateway=environment_gateway)
        self.graph = build_eldercare_butler_graph(
            self.actions,
            trace_store=self.trace_store,
        )

    async def ingest(self, reading: SensorReading) -> dict[str, Any]:
        self.store.add_reading(reading)
        events = self.monitor.evaluate_resident(
            reading.resident_id,
            now=reading.observed_at,
        )
        tasks = [await self.process_event(event) for event in events]
        return {
            "reading": reading.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
            "tasks": tasks,
        }

    async def process_event(self, event: RiskEvent) -> dict[str, Any]:
        task_id = f"eldercare_{uuid4().hex[:12]}"
        await self.trace_store.create(
            task_id=task_id,
            session_id=f"resident:{event.resident_id}",
            request_id=event.event_id,
        )
        await self.trace_store.set_status(task_id, "running")
        await self.trace_store.record(
            task_id=task_id,
            type="task.started",
            node="health_monitor",
            data={"trigger": "risk_event", "event": event.model_dump(mode="json")},
        )
        try:
            result = await self.graph.ainvoke({
                "task_id": task_id,
                "event": event.model_dump(mode="json"),
                "child_results": [],
            })
            status = str(result.get("status", "failed"))
            await self.trace_store.record(
                task_id=task_id,
                type=f"task.{status}",
                node="butler",
                data={"response": result.get("response", "")},
            )
            payload = {
                "task_id": task_id,
                "status": status,
                "response": result.get("response", ""),
                "event_id": event.event_id,
                "child_results": result.get("child_results", []),
                "denied_delegations": result.get("denied_delegations", []),
            }
            await self.trace_store.finish(task_id, status=status, result=payload)
            return payload
        except Exception as exc:
            payload = {"task_id": task_id, "status": "failed", "error": str(exc)}
            await self.trace_store.record(
                task_id=task_id,
                type="task.failed",
                node="butler",
                data={"error": str(exc)},
            )
            await self.trace_store.finish(task_id, status="failed", result=payload)
            return payload

    async def execute_user_delegations(
        self,
        *,
        task_id: str,
        resident_id: str,
        message: str,
        delegations: list[dict[str, Any]],
        role: str = "operator",
    ) -> dict[str, Any]:
        event = RiskEvent(
            resident_id=resident_id,
            event_type="user_request",
            severity="medium",
            value=True,
            message=message,
            source="user-command",
            context={"requested_delegations": delegations},
        )
        return await self.graph.ainvoke({
            "task_id": task_id,
            "user_role": role,
            "event": event.model_dump(mode="json"),
            "child_results": [],
        })

    async def scan(self) -> list[dict[str, Any]]:
        return [await self.process_event(event) for event in self.monitor.evaluate_all()]

    async def monitor_forever(
        self,
        stop_event: asyncio.Event,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        while not stop_event.is_set():
            await self.scan()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass

    async def simulate(self, scenario: str, resident_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        readings: list[SensorReading]
        if scenario == "high_heart_rate":
            readings = [
                SensorReading(
                    resident_id=resident_id,
                    sensor_type="heart_rate",
                    value=value,
                    unit="bpm",
                    source="scenario-simulator",
                    observed_at=now - timedelta(minutes=minutes),
                )
                for minutes, value in ((5, 103), (3, 105), (1, 108), (0, 106))
            ]
        elif scenario == "fall":
            readings = [SensorReading(
                resident_id=resident_id,
                sensor_type="fall_detected",
                value=True,
                source="scenario-simulator",
                observed_at=now,
            )]
        elif scenario == "hot_room":
            readings = [SensorReading(
                resident_id=resident_id,
                sensor_type="room_temperature",
                value=30.5,
                unit="C",
                source="scenario-simulator",
                observed_at=now,
            )]
        elif scenario == "low_spo2":
            readings = [SensorReading(
                resident_id=resident_id,
                sensor_type="spo2",
                value=89,
                unit="%",
                source="scenario-simulator",
                observed_at=now,
            )]
        elif scenario == "normal":
            readings = [
                SensorReading(resident_id=resident_id, sensor_type="heart_rate", value=78, unit="bpm", observed_at=now),
                SensorReading(resident_id=resident_id, sensor_type="spo2", value=97, unit="%", observed_at=now),
                SensorReading(resident_id=resident_id, sensor_type="fall_detected", value=False, observed_at=now),
                SensorReading(resident_id=resident_id, sensor_type="room_temperature", value=24, unit="C", observed_at=now),
            ]
        else:
            raise ValueError(f"unknown eldercare scenario: {scenario}")

        events: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        for reading in readings:
            result = await self.ingest(reading)
            events.extend(result["events"])
            tasks.extend(result["tasks"])
        return {
            "scenario": scenario,
            "resident_id": resident_id,
            "readings": [reading.model_dump(mode="json") for reading in readings],
            "events": events,
            "tasks": tasks,
        }
