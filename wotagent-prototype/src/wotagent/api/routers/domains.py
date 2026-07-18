"""Generic domain operations API used by the platform console."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...domain import (
    DomainAccessDenied,
    DomainNotFound,
    DomainOperationsRegistry,
)


class TelemetryIngestRequest(BaseModel):
    subject_id: str
    signal: str
    value: float | bool | str
    unit: str = ""
    source: str = "domain-console"
    principal_id: str = "local-user"


class ScenarioRequest(BaseModel):
    subject_id: str
    scenario: str
    principal_id: str = "local-user"


class EventAcknowledgeRequest(BaseModel):
    principal_id: str = "local-user"


def create_domain_router(
    get_registry: Callable[[], DomainOperationsRegistry],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/domains", tags=["domains"])

    def adapter(domain_id: str):
        try:
            return get_registry().require(domain_id)
        except DomainNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def guarded(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except DomainAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def guarded_async(call: Callable[[], Any]) -> Any:
        try:
            return await call()
        except DomainAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("")
    async def list_domains() -> dict[str, Any]:
        return {
            "domains": [
                item.model_dump(mode="json")
                for item in get_registry().manifests()
            ]
        }

    @router.get("/{domain_id}/subjects")
    async def list_subjects(
        domain_id: str,
        principal_id: str = "local-user",
    ) -> dict[str, Any]:
        return {
            "domain_id": domain_id,
            "subjects": guarded(
                lambda: adapter(domain_id).list_subjects(principal_id)
            ),
        }

    @router.get("/{domain_id}/subjects/{subject_id}/overview")
    async def get_overview(
        domain_id: str,
        subject_id: str,
        principal_id: str = "local-user",
    ) -> dict[str, Any]:
        return guarded(
            lambda: adapter(domain_id).overview(
                subject_id,
                principal_id,
            )
        )

    @router.get("/{domain_id}/subjects/{subject_id}/resources")
    async def list_resources(
        domain_id: str,
        subject_id: str,
        principal_id: str = "local-user",
    ) -> dict[str, Any]:
        return {
            "domain_id": domain_id,
            "subject_id": subject_id,
            "resources": await guarded_async(
                lambda: adapter(domain_id).resources(
                    subject_id,
                    principal_id,
                )
            ),
        }

    @router.get("/{domain_id}/subjects/{subject_id}/events")
    async def list_events(
        domain_id: str,
        subject_id: str,
        principal_id: str = "local-user",
    ) -> dict[str, Any]:
        return {
            "domain_id": domain_id,
            "subject_id": subject_id,
            "events": guarded(
                lambda: adapter(domain_id).events(
                    subject_id,
                    principal_id,
                )
            ),
        }

    @router.get("/{domain_id}/subjects/{subject_id}/actions")
    async def list_actions(
        domain_id: str,
        subject_id: str,
        principal_id: str = "local-user",
    ) -> dict[str, Any]:
        return {
            "domain_id": domain_id,
            "subject_id": subject_id,
            "actions": guarded(
                lambda: adapter(domain_id).actions(
                    subject_id,
                    principal_id,
                )
            ),
        }

    @router.get("/{domain_id}/monitor/status")
    async def monitor_status(domain_id: str) -> dict[str, Any]:
        return guarded(lambda: adapter(domain_id).monitor_status())

    @router.post("/{domain_id}/telemetry", status_code=202)
    async def ingest_telemetry(
        domain_id: str,
        request: TelemetryIngestRequest,
    ) -> dict[str, Any]:
        return await guarded_async(
            lambda: adapter(domain_id).ingest(
                request.subject_id,
                request.principal_id,
                request.model_dump(mode="json"),
            )
        )

    @router.post("/{domain_id}/scenarios", status_code=202)
    async def simulate(
        domain_id: str,
        request: ScenarioRequest,
    ) -> dict[str, Any]:
        return await guarded_async(
            lambda: adapter(domain_id).simulate(
                request.subject_id,
                request.principal_id,
                request.scenario,
            )
        )

    @router.post("/{domain_id}/events/{event_id}/acknowledge")
    async def acknowledge(
        domain_id: str,
        event_id: str,
        request: EventAcknowledgeRequest,
    ) -> dict[str, Any]:
        return guarded(
            lambda: adapter(domain_id).acknowledge(
                event_id,
                request.principal_id,
            )
        )

    return router


__all__ = ["create_domain_router"]
