"""Read-only experiment identity API for the operations console."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from ...identity import PrincipalDirectory, PrincipalNotAssigned


def create_principal_router(
    get_directory: Callable[[], PrincipalDirectory],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/principals", tags=["principals"])

    @router.get("")
    async def list_principals(
        domain_id: str,
        login_only: bool = True,
    ):
        return {
            "domain_id": domain_id,
            "principals": [
                item.model_dump(mode="json")
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


__all__ = ["create_principal_router"]
