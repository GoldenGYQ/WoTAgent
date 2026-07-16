from __future__ import annotations

import asyncio

from wotagent.eldercare import EldercareDeviceCollector, EldercareStore, Resident


class FakeGateway:
    def __init__(self) -> None:
        self.states = {
            "wristband-001": {
                "heartRate": 108,
                "spo2": 96,
                "fallDetected": False,
                "observedAt": "2026-07-16T10:00:00+00:00",
                "_mcp_tool": "wristband_001__read_state",
            },
            "mattress-001": {
                "bedOccupancy": True,
                "sleepQuality": "poor",
                "observedAt": "2026-07-16T10:00:00+00:00",
            },
            "environment-sensor-001": {
                "roomTemperature": 30.5,
                "roomHumidity": 55,
                "observedAt": "2026-07-16T10:00:00+00:00",
            },
        }

    async def query_state(self, device_id):
        return dict(self.states[device_id])


def test_collector_maps_td_properties_to_standard_sensor_readings(tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    resident = store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        wristband_device_id="wristband-001",
        mattress_device_id="mattress-001",
        environment_sensor_device_id="environment-sensor-001",
    ))
    collector = EldercareDeviceCollector(store, FakeGateway())

    first = asyncio.run(collector.collect_resident(resident))
    second = asyncio.run(collector.collect_resident(resident))

    assert {item.sensor_type for item in first} == {
        "heart_rate",
        "spo2",
        "fall_detected",
        "bed_occupancy",
        "sleep_quality",
        "room_temperature",
        "room_humidity",
    }
    assert all(item.source.startswith("wot-mcp:") for item in first)
    assert second == []
