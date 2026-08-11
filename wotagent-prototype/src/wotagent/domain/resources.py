"""Resolve trusted principal resources into deployment-scoped devices."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ..identity import TaskPrincipal
from ..wot import ThingDescription, locations_match

ResourceLocationResolver = Callable[[str], str | None]


class DeploymentResourceResolver:
    """Expand principal resources through canonical TD locations.

    The resolver does not decide whether a capability is allowed. It only turns
    already-attested resources such as ``resident:elderly-001`` or
    ``space:A-101`` into device IDs within the active deployment profile.
    """

    def __init__(self, thing_descriptions: Iterable[ThingDescription]) -> None:
        self._locations = {
            td.device_id: td.location.strip()
            for td in thing_descriptions
            if td.device_id and td.location.strip()
        }

    def resolve_device_ids(
        self,
        *,
        principal: TaskPrincipal,
        profile_device_ids: Iterable[str],
        site_id: str,
        resource_location: ResourceLocationResolver,
    ) -> list[str]:
        profile_devices = set(profile_device_ids)
        if "*" in principal.resource_ids:
            return sorted(profile_devices)
        if f"site:{site_id}" in principal.resource_ids:
            return sorted(profile_devices)

        resource_ids = set(principal.resource_ids)
        resource_ids.update(
            relationship.resource_id
            for relationship in principal.relationships
        )
        device_ids = {
            resource_id.removeprefix("device:")
            for resource_id in resource_ids
            if resource_id.startswith("device:")
        }
        locations = {
            location.strip()
            for resource_id in resource_ids
            if (location := resource_location(resource_id))
        }
        device_ids.update(
            device_id
            for device_id, location in self._locations.items()
            if any(locations_match(location, allowed) for allowed in locations)
        )
        return sorted(device_ids.intersection(profile_devices))


__all__ = ["DeploymentResourceResolver", "ResourceLocationResolver"]
