"""Experiment-grade principal-to-domain-resource assignments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainAssignment:
    principal_id: str
    resource_ids: frozenset[str]
    capabilities: frozenset[str]


class DomainAssignmentService:
    def __init__(self, assignments: list[DomainAssignment]) -> None:
        self._assignments = {
            assignment.principal_id: assignment
            for assignment in assignments
        }

    def allows(
        self,
        principal_id: str,
        capability: str,
        resource_id: str,
    ) -> bool:
        assignment = self._assignments.get(principal_id)
        if assignment is None:
            return False
        resource_allowed = (
            "*" in assignment.resource_ids
            or resource_id in assignment.resource_ids
        )
        capability_allowed = (
            "*" in assignment.capabilities
            or capability in assignment.capabilities
        )
        return resource_allowed and capability_allowed

    def capabilities_for(
        self,
        principal_id: str,
        resource_id: str,
    ) -> list[str]:
        assignment = self._assignments.get(principal_id)
        if assignment is None:
            return []
        if (
            "*" not in assignment.resource_ids
            and resource_id not in assignment.resource_ids
        ):
            return []
        return sorted(assignment.capabilities)


VILLA_ASSIGNMENTS = [
    DomainAssignment(
        principal_id="JH-1",
        resource_ids=frozenset({"site:villa-demo"}),
        capabilities=frozenset({
            "domain.read",
            "device.read",
            "device.control",
            "scenario.execute",
            "event.acknowledge",
        }),
    ),
    DomainAssignment(
        principal_id="JH-2",
        resource_ids=frozenset({"site:villa-demo"}),
        capabilities=frozenset({
            "domain.read",
            "device.read",
        }),
    ),
    DomainAssignment(
        principal_id="local-user",
        resource_ids=frozenset({"site:villa-demo"}),
        capabilities=frozenset({"*"}),
    ),
]


__all__ = [
    "DomainAssignment",
    "DomainAssignmentService",
    "VILLA_ASSIGNMENTS",
]
