"""Collect eldercare sensor state through the shared WoT/MCP device gateway."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from .models import Resident, SensorReading
from .store import EldercareStore


DEVICE_FIELDS = (
    "wristband_device_id",
    "mattress_device_id",
    "environment_sensor_device_id",
)

PROPERTY_BINDINGS: dict[str, tuple[str, str]] = {
    "heartRate": ("heart_rate", "bpm"),
    "spo2": ("spo2", "%"),
    "fallDetected": ("fall_detected", ""),
    "bedOccupancy": ("bed_occupancy", ""),
    "sleepQuality": ("sleep_quality", ""),
    "roomTemperature": ("room_temperature", "C"),
    "roomHumidity": ("room_humidity", "%"),
}


class EldercareDeviceCollector:
    def __init__(self, store: EldercareStore, gateway: Any) -> None:
        self.store = store
        self.gateway = gateway
        self._fingerprints: dict[str, str] = {}

    async def collect_all(self) -> list[SensorReading]:
        readings: list[SensorReading] = []
        for resident in self.store.list_residents():
            readings.extend(await self.collect_resident(resident))
        return readings

    async def collect_resident(self, resident: Resident) -> list[SensorReading]:
        readings: list[SensorReading] = []
        for field in DEVICE_FIELDS:
            device_id = getattr(resident, field)
            if device_id:
                readings.extend(await self.collect_device(resident, device_id))
        return readings

    async def collect_device(
        self,
        resident: Resident,
        device_id: str,
        *,
        force: bool = False,
    ) -> list[SensorReading]:
        state = await self.gateway.query_state(device_id)
        payload = {
            key: value for key, value in state.items()
            if not key.startswith("_")
        }
        fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if not force and self._fingerprints.get(device_id) == fingerprint:
            return []
        self._fingerprints[device_id] = fingerprint

        observed_at = _observed_at(payload.get("observedAt"))
        readings: list[SensorReading] = []
        for property_name, (sensor_type, unit) in PROPERTY_BINDINGS.items():
            if property_name not in payload:
                continue
            readings.append(SensorReading(
                resident_id=resident.resident_id,
                sensor_type=sensor_type,
                value=payload[property_name],
                unit=unit,
                source=f"wot-mcp:{device_id}",
                observed_at=observed_at,
                metadata={
                    "device_id": device_id,
                    "property": property_name,
                },
            ))
        return readings

    async def monitor_forever(
        self,
        stop_event: asyncio.Event,
        on_reading: Any,
        *,
        interval_seconds: float = 2.0,
    ) -> None:
        while not stop_event.is_set():
            try:
                for reading in await self.collect_all():
                    await on_reading(reading)
            except Exception:
                # A temporary gateway outage must not terminate the API lifespan.
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass


def _observed_at(value: Any) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        except ValueError:
            pass
    return datetime.now(UTC)
