"""Read-only experiment identity API for the operations console."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...identity import (
    PrincipalAuthenticationError,
    PrincipalDirectory,
    PrincipalNotAssigned,
    PrincipalRegistrationError,
    PrincipalStore,
    registration_targets,
    role_templates,
)


class PrincipalRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=2, max_length=30)
    pin: str = Field(min_length=4, max_length=12)
    domain_id: str = Field(min_length=1)
    role_template_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)


class PrincipalLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    pin: str | None = None


def create_principal_router(
    get_directory: Callable[[], PrincipalDirectory],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/principals", tags=["principals"])

    def store() -> PrincipalStore:
        directory = get_directory()
        if not isinstance(directory, PrincipalStore):
            raise HTTPException(
                status_code=501,
                detail="persistent principal registration is unavailable",
            )
        return directory

    @router.get("/role-templates")
    async def list_role_templates(domain_id: str):
        return {
            "domain_id": domain_id,
            "templates": [
                item.model_dump(mode="json")
                for item in role_templates(domain_id)
            ],
        }

    @router.get("/registration-targets")
    async def list_registration_targets(domain_id: str):
        return {
            "domain_id": domain_id,
            "targets": [
                item.model_dump(mode="json")
                for item in registration_targets(domain_id)
            ],
        }

    @router.post("/register", status_code=201)
    async def register_principal(request: PrincipalRegisterRequest):
        try:
            principal, token = store().register(
                display_name=request.display_name,
                pin=request.pin,
                domain_id=request.domain_id,
                role_template_id=request.role_template_id,
                target_id=request.target_id,
            )
        except PrincipalRegistrationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "token": token,
            "login_mode": "pin",
            "principal": principal.model_dump(mode="json"),
        }

    @router.post("/login")
    async def login_principal(request: PrincipalLoginRequest):
        try:
            principal, token = store().login(
                principal_id=request.principal_id,
                domain_id=request.domain_id,
                pin=request.pin,
            )
        except PrincipalAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {
            "token": token,
            "login_mode": store().login_mode(request.principal_id),
            "principal": principal.model_dump(mode="json"),
        }

    @router.get("/session")
    async def current_session(
        domain_id: str,
        authorization: str | None = Header(default=None),
    ):
        token = _bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="missing login session")
        try:
            principal = store().resolve_session(token, domain_id)
        except PrincipalAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {
            "login_mode": store().login_mode(principal.principal_id),
            "principal": principal.model_dump(mode="json"),
        }

    @router.get("")
    async def list_principals(
        domain_id: str,
        login_only: bool = True,
    ):
        return {
            "domain_id": domain_id,
            "principals": [
                {
                    **item.model_dump(mode="json"),
                    "login_mode": get_directory().login_mode(
                        item.principal_id
                    ),
                }
                for item in get_directory().list_contexts(
                    domain_id,
                    login_only=login_only,
                )
            ],
        }

    @router.get("/{principal_id}/context")
    async def get_principal_context(
        principal_id: str,
        domain_id: str,
    ):
        try:
            context = get_directory().resolve(
                principal_id,
                domain_id,
            )
        except PrincipalNotAssigned as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return context.model_dump(mode="json")

    return router


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


__all__ = ["create_principal_router"]
