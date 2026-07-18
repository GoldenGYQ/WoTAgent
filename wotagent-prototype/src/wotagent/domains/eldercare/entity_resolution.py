"""Resolve resident mentions without allowing the LLM to invent canonical IDs."""

from __future__ import annotations

from ...orchestration.run_models import TaskTarget
from ...domain.entity_resolution import EntityResolutionError
from .store import EldercareStore


class ResidentEntityResolver:
    def __init__(self, store: EldercareStore) -> None:
        self.store = store

    def resolve(
        self,
        *,
        target_type: str,
        mention: str | None,
        text: str,
    ) -> TaskTarget:
        if target_type != "resident":
            raise EntityResolutionError(
                "ENTITY_TYPE_UNSUPPORTED",
                f"unsupported eldercare target type: {target_type}",
            )
        residents = self.store.list_residents()
        explicit = (mention or "").strip().lower()
        haystack = text.lower()
        matches = [
            resident
            for resident in residents
            if (
                explicit
                and explicit in {
                    resident.resident_id.lower(),
                    resident.name.lower(),
                }
            )
            or resident.resident_id.lower() in haystack
            or resident.name.lower() in haystack
        ]
        unique = {item.resident_id: item for item in matches}
        if not unique:
            raise EntityResolutionError(
                "ENTITY_NOT_FOUND",
                "无法把任务中的居民名称解析为规范 resident_id。",
            )
        if len(unique) > 1:
            raise EntityResolutionError(
                "ENTITY_AMBIGUOUS",
                "任务中匹配到多个居民，需要用户明确指定。",
            )
        resident = next(iter(unique.values()))
        return TaskTarget(
            type="resident",
            id=resident.resident_id,
            display_name=resident.name,
        )


__all__ = ["ResidentEntityResolver"]
