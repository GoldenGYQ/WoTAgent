"""Resolve resident mentions without allowing the LLM to invent canonical IDs."""

from __future__ import annotations

from ...domain.entity_resolution import EntityResolutionError
from ...orchestration.run_models import TaskTarget
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
                    _normalize_room_token(resident.room),
                }
            )
            or resident.resident_id.lower() in haystack
            or resident.name.lower() in haystack
            or _room_matches(haystack, resident.room)
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


def _room_matches(text: str, room: str) -> bool:
    normalized_text = _normalize_room_token(text)
    normalized_room = _normalize_room_token(room)
    if not normalized_room:
        return False
    return normalized_room in normalized_text


def _normalize_room_token(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


__all__ = ["ResidentEntityResolver"]
