"""Minimal capability and resident-relationship authorization service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel

from ..orchestration.run_models import (
    ResourceScope,
    TaskPrincipal,
    TaskTarget,
)


class AuthorizationDecision(BaseModel):
    effect: Literal["allow", "deny", "confirm"]
    reason: str
    effective_scope: ResourceScope | None = None


class RelationshipAuthorizationService:
    """Small P0 policy service; identities are still demo identities."""

    def __init__(
        self,
        resident_assignments: Mapping[str, set[str]],
    ) -> None:
        self.resident_assignments = {
            principal_id: set(resident_ids)
            for principal_id, resident_ids in resident_assignments.items()
        }

    def check(
        self,
        *,
        principal: TaskPrincipal,
        capability: str,
        target: TaskTarget,
    ) -> AuthorizationDecision:
        if "admin" in principal.roles:
            return self._allow(capability, target, "admin role")
        resource_id = f"{target.type}:{target.id}"
        if principal.granted_capabilities:
            if principal.allows(capability, resource_id):
                return self._allow(
                    capability,
                    target,
                    "principal capability and relationship scope",
                )
            return AuthorizationDecision(
                effect="deny",
                reason=(
                    f"principal '{principal.principal_id}' lacks capability "
                    f"'{capability}' or resource '{resource_id}'"
                ),
            )
        assigned = self.resident_assignments.get(principal.principal_id, set())
        if target.id not in assigned:
            return AuthorizationDecision(
                effect="deny",
                reason=(
                    f"principal '{principal.principal_id}' is not assigned "
                    f"to resident '{target.id}'"
                ),
            )
        health_capabilities = {
            "health.status.read",
            "health.summary.read",
            "health.vitals.read",
            "health.trend.read",
            "health.data_quality.read",
            "resident.context.read",
        }
        if capability in health_capabilities and set(
            principal.roles
        ).intersection({"viewer", "operator"}):
            return self._allow(capability, target, "assigned health access")
        if capability in {"care.coordinate", "environment.control"} and (
            "operator" in principal.roles
        ):
            return self._allow(capability, target, "assigned operator access")
        return AuthorizationDecision(
            effect="deny",
            reason=(
                f"roles {principal.roles} do not grant capability "
                f"'{capability}'"
            ),
        )

    @staticmethod
    def _allow(
        capability: str,
        target: TaskTarget,
        reason: str,
    ) -> AuthorizationDecision:
        return AuthorizationDecision(
            effect="allow",
            reason=reason,
            effective_scope=ResourceScope(
                resource_ids=[f"{target.type}:{target.id}"],
                capabilities=[capability],
            ),
        )


__all__ = [
    "AuthorizationDecision",
    "RelationshipAuthorizationService",
]
