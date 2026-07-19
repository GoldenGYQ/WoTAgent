from __future__ import annotations

import os
from collections.abc import Callable

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...domain import DeploymentProfileRegistry


class ActivateDeploymentRequest(BaseModel):
    profile_id: str


def create_deployment_router(
    get_registry: Callable[[], DeploymentProfileRegistry],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/deployment", tags=["deployment"])

    @router.get("/profiles")
    async def list_profiles():
        registry = get_registry()
        return {
            "default_profile_id": registry.default_profile_id,
            "profiles": registry.list(),
        }

    @router.put("/active")
    async def activate_profile(request: ActivateDeploymentRequest):
        registry = get_registry()
        try:
            profile = registry.require(request.profile_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        simulator_url = os.getenv(
            "WOT_SIMULATOR_URL",
            "http://127.0.0.1:18080",
        ).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.put(
                    f"{simulator_url}/api/scenes/active",
                    json={"profile_id": profile.profile_id},
                )
                response.raise_for_status()
                simulator = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"simulator scene switch failed: {exc}",
            ) from exc
        return {"profile": profile, "simulator": simulator}

    return router


__all__ = ["create_deployment_router"]
