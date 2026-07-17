from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ...domains.eldercare import EldercareRuntime, EldercareStore, Resident, SensorReading
from ...gateway import MCPDeviceGateway
from ..schemas import EldercareScenarioRequest


def create_eldercare_router(
    *,
    get_store: Callable[[], EldercareStore],
    get_runtime: Callable[[], EldercareRuntime],
    get_gateway: Callable[[], MCPDeviceGateway],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/eldercare", tags=["eldercare"])

    @router.get("/residents")
    async def list_residents() -> dict[str, Any]:
        return {
            "residents": [
                item.model_dump(mode="json")
                for item in get_store().list_residents()
            ]
        }

    @router.post("/residents", status_code=201)
    async def upsert_resident(resident: Resident) -> dict[str, Any]:
        return get_store().upsert_resident(resident).model_dump(mode="json")

    @router.get("/residents/{resident_id}/summary")
    async def get_summary(resident_id: str) -> dict[str, Any]:
        try:
            return get_store().summary(resident_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/readings", status_code=202)
    async def ingest_reading(reading: SensorReading) -> dict[str, Any]:
        try:
            return await get_runtime().publish_reading(reading)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/simulate", status_code=202)
    async def simulate_scenario(request: EldercareScenarioRequest) -> dict[str, Any]:
        try:
            return await get_runtime().simulate(
                request.scenario,
                request.resident_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/events")
    async def list_events(
        resident_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return {
            "events": [
                item.model_dump(mode="json")
                for item in get_store().list_risks(
                    resident_id,
                    limit=min(max(limit, 1), 500),
                )
            ]
        }

    @router.get("/actions")
    async def list_actions(
        resident_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return {
            "actions": get_store().list_actions(
                resident_id,
                limit=min(max(limit, 1), 500),
            )
        }

    @router.get("/outbox")
    async def list_outbox(limit: int = 100) -> dict[str, Any]:
        return {"events": get_store().list_outbox(limit=limit)}

    @router.get("/monitor/status")
    async def get_monitor_status() -> dict[str, Any]:
        status = get_store().service_status("eldercare-monitor")
        return status or {
            "service_id": "eldercare-monitor",
            "status": "offline",
            "reported_status": "unknown",
            "details": {},
            "updated_at": None,
            "age_seconds": None,
        }

    @router.post("/events/{event_id}/acknowledge")
    async def acknowledge_event(event_id: str) -> dict[str, Any]:
        event = get_store().handle_risk(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="risk event not found")
        return event.model_dump(mode="json")

    @router.get("/residents/{resident_id}/devices")
    async def list_devices(resident_id: str) -> dict[str, Any]:
        resident = get_store().get_resident(resident_id)
        if resident is None:
            raise HTTPException(status_code=404, detail="resident not found")
        mapped_devices = [
            (resident.wristband_device_id, "sensor"),
            (resident.mattress_device_id, "sensor"),
            (resident.environment_sensor_device_id, "sensor"),
            (resident.environment_device_id, "actuator"),
        ]
        gateway = get_gateway()
        devices = []
        try:
            capabilities = await gateway.list_devices()
        except Exception:
            capabilities = []
        by_id = {str(item.get("id")): item for item in capabilities}
        for device_id, kind in mapped_devices:
            if not device_id:
                continue
            capability = by_id.get(device_id, {})
            try:
                state = await gateway.query_state(device_id)
                observed_at = state.get("observedAt") or datetime.now(UTC).isoformat()
                devices.append({
                    "device_id": device_id,
                    "name": capability.get("title") or device_id,
                    "kind": kind,
                    "transport": "WoT TD / MCP",
                    "status": _reading_status(str(observed_at)),
                    "last_seen": observed_at,
                    "capabilities": [
                        item.get("name")
                        for item in capability.get("actions", [])
                    ],
                    "state": state,
                })
            except Exception as exc:
                devices.append({
                    "device_id": device_id,
                    "name": capability.get("title") or device_id,
                    "kind": kind,
                    "transport": "WoT TD / MCP",
                    "status": "offline",
                    "last_seen": None,
                    "capabilities": [],
                    "state": {"error": str(exc)},
                })
        return {"resident_id": resident_id, "devices": devices}

    return router


def _reading_status(last_seen: str | None) -> str:
    if not last_seen:
        return "no_data"
    observed_at = datetime.fromisoformat(last_seen)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - observed_at).total_seconds()
    return "online" if age_seconds <= 600 else "stale"


__all__ = ["create_eldercare_router"]
