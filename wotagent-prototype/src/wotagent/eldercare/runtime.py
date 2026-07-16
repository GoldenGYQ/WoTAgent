"""Event-driven runtime joining sensor ingestion, monitoring, and Butler delegation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..telemetry import InMemoryTraceStore
from .actions import EldercareActionService
from .collector import EldercareDeviceCollector
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
        self.environment_gateway = environment_gateway
        self.collector = (
            EldercareDeviceCollector(store, environment_gateway)
            if environment_gateway is not None
            else None
        )
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

    async def consume_outbox_once(self, *, limit: int = 10) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in self.store.claim_events(limit=limit):
            try:
                event = RiskEvent.model_validate(item["payload"])
                result = await self.process_event(event)
                if result.get("status") == "failed":
                    raise RuntimeError(str(result.get("error", "butler event processing failed")))
                self.store.complete_event(item["outbox_id"])
                results.append(result)
            except Exception as exc:
                self.store.retry_event(item["outbox_id"], str(exc))
        return results

    async def consume_outbox_forever(
        self,
        stop_event: asyncio.Event,
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        while not stop_event.is_set():
            await self.consume_outbox_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass

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

    async def collect_devices(self) -> list[dict[str, Any]]:
        if self.collector is None:
            return []
        results: list[dict[str, Any]] = []
        for reading in await self.collector.collect_all():
            results.append(await self.ingest(reading))
        return results

    async def collect_devices_forever(
        self,
        stop_event: asyncio.Event,
        *,
        interval_seconds: float = 2.0,
    ) -> None:
        if self.collector is None:
            return
        await self.collector.monitor_forever(
            stop_event,
            self.ingest,
            interval_seconds=interval_seconds,
        )

    async def publish_reading(self, reading: SensorReading) -> dict[str, Any]:
        if self.collector is None or self.environment_gateway is None:
            raise RuntimeError("eldercare WoT/MCP sensor gateway is not configured")
        resident = self.store.get_resident(reading.resident_id)
        if resident is None:
            raise KeyError(f"resident '{reading.resident_id}' not found")

        bindings = {
            "heart_rate": (resident.wristband_device_id, "updateVitals", "heartRate"),
            "spo2": (resident.wristband_device_id, "updateVitals", "spo2"),
            "fall_detected": (resident.wristband_device_id, "updateVitals", "fallDetected"),
            "bed_occupancy": (resident.mattress_device_id, "updateSleepState", "bedOccupancy"),
            "sleep_quality": (resident.mattress_device_id, "updateSleepState", "sleepQuality"),
            "room_temperature": (
                resident.environment_sensor_device_id,
                "updateEnvironment",
                "roomTemperature",
            ),
            "room_humidity": (
                resident.environment_sensor_device_id,
                "updateEnvironment",
                "roomHumidity",
            ),
        }
        device_id, action, property_name = bindings[reading.sensor_type]
        if not device_id:
            raise RuntimeError(
                f"resident '{reading.resident_id}' has no device for {reading.sensor_type}"
            )
        await self.environment_gateway.control_device(
            device_id,
            action,
            {
                property_name: reading.value,
                "observedAt": reading.observed_at.astimezone(UTC).isoformat(),
            },
            idempotency_key=f"sensor:{reading.reading_id}",
        )
        collected = await self.collector.collect_device(resident, device_id, force=True)
        return {
            "device_id": device_id,
            "reading": reading.model_dump(mode="json"),
            "collected": [item.model_dump(mode="json") for item in collected],
            "events": [],
            "tasks": [],
            "delivery": "monitor_pending",
        }

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
            result = (
                await self.publish_reading(reading)
                if self.collector is not None
                else await self.ingest(reading)
            )
            events.extend(result["events"])
            tasks.extend(result["tasks"])
        return {
            "scenario": scenario,
            "resident_id": resident_id,
            "readings": [reading.model_dump(mode="json") for reading in readings],
            "events": events,
            "tasks": tasks,
        }
