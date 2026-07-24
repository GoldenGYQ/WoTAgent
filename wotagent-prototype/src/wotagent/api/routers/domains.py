"""Generic domain operations API used by the platform console."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from ...domain import (
    DomainAccessDenied,
    DomainNotFound,
    DomainPluginRegistry,
)
from ...identity import (
    PrincipalAuthenticationError,
    PrincipalDirectory,
    PrincipalNotAssigned,
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


class EventStatusUpdateRequest(BaseModel):
    principal_id: str = "local-user"
    status: Literal[
        "acknowledged",
        "in_progress",
        "resolved",
        "false_positive",
    ]


def create_domain_router(
    get_registry: Callable[[], DomainPluginRegistry],
    get_principals: Callable[[], PrincipalDirectory] | None = None,
    get_run_service: Callable[[], Any] | None = None,
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

    def authorized_principal(
        domain_id: str,
        principal_id: str,
        authorization: str | None,
    ) -> str:
        if get_principals is None:
            return principal_id
        directory = get_principals()
        resolver = getattr(directory, "resolve_for_request", None)
        if not callable(resolver):
            return principal_id
        try:
            principal = resolver(
                principal_id=principal_id,
                domain_id=domain_id,
                token=_bearer_token(authorization),
                fallback_role="viewer",
            )
        except PrincipalAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PrincipalNotAssigned as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return principal.principal_id

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
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            principal_id,
            authorization,
        )
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
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            principal_id,
            authorization,
        )
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
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            principal_id,
            authorization,
        )
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
        severity: Literal["medium", "high", "critical"] | None = None,
        status: Literal[
            "open",
            "acknowledged",
            "in_progress",
            "resolved",
            "false_positive",
        ] | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            principal_id,
            authorization,
        )
        return {
            "domain_id": domain_id,
            "subject_id": subject_id,
            "events": guarded(
                lambda: adapter(domain_id).events(
                    subject_id,
                    principal_id,
                    severity,
                    status,
                )
            ),
        }

    @router.get("/{domain_id}/subjects/{subject_id}/actions")
    async def list_actions(
        domain_id: str,
        subject_id: str,
        principal_id: str = "local-user",
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            principal_id,
            authorization,
        )
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
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            request.principal_id,
            authorization,
        )
        return await guarded_async(
            lambda: adapter(domain_id).ingest(
                request.subject_id,
                principal_id,
                request.model_dump(mode="json"),
            )
        )

    @router.post("/{domain_id}/scenarios", status_code=202)
    async def simulate(
        domain_id: str,
        request: ScenarioRequest,
        background_tasks: BackgroundTasks,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            request.principal_id,
            authorization,
        )
        domain_adapter = adapter(domain_id)
        prepare_run = getattr(
            domain_adapter,
            "prepare_scenario_run",
            None,
        )
        if get_run_service is not None and callable(prepare_run):
            run_request = guarded(
                lambda: prepare_run(
                    request.subject_id,
                    principal_id,
                    request.scenario,
                )
            )
            if run_request is not None:
                run = await get_run_service().create_domain_scenario_run(
                    message=run_request.goal,
                    domain_id=domain_id,
                    scenario=request.scenario,
                    source_context=run_request.source_context,
                    user_id=principal_id,
                    background_tasks=background_tasks,
                )
                return {
                    "scenario": request.scenario,
                    "execution_mode": "agent_run",
                    "run_id": run.run_id,
                    "conversation_id": run.conversation_id,
                    "status": run.status,
                }
        return await guarded_async(
            lambda: domain_adapter.simulate(
                request.subject_id,
                principal_id,
                request.scenario,
            )
        )

    @router.post("/{domain_id}/events/{event_id}/acknowledge")
    async def acknowledge(
        domain_id: str,
        event_id: str,
        request: EventAcknowledgeRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            request.principal_id,
            authorization,
        )
        return guarded(
            lambda: adapter(domain_id).acknowledge(
                event_id,
                principal_id,
            )
        )

    @router.post("/{domain_id}/events/{event_id}/status")
    async def update_event_status(
        domain_id: str,
        event_id: str,
        request: EventStatusUpdateRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        principal_id = authorized_principal(
            domain_id,
            request.principal_id,
            authorization,
        )
        return guarded(
            lambda: adapter(domain_id).update_event_status(
                event_id,
                principal_id,
                request.status,
            )
        )

    return router


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token else None


__all__ = ["create_domain_router"]
