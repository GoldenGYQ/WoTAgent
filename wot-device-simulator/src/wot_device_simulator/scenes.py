"""Shared deployment-profile state for simulator REST and MCP surfaces."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def profile_catalog_path() -> Path:
    override = os.getenv("WOT_SIM_DEPLOYMENT_PROFILES", "").strip()
    if override:
        return Path(override)
    return _project_root().parent / "config" / "deployment_profiles.json"


@dataclass(frozen=True)
class SimulatorScene:
    profile_id: str
    domain_id: str
    site_id: str
    title: str
    description: str
    spaces: tuple[str, ...]
    device_ids: frozenset[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "domain_id": self.domain_id,
            "site_id": self.site_id,
            "title": self.title,
            "description": self.description,
            "spaces": list(self.spaces),
            "device_ids": sorted(self.device_ids),
        }


class SimulatorSceneRegistry:
    def __init__(self) -> None:
        payload = json.loads(
            profile_catalog_path().read_text(encoding="utf-8")
        )
        self._scenes = {
            str(item["profile_id"]): SimulatorScene(
                profile_id=str(item["profile_id"]),
                domain_id=str(item["domain_id"]),
                site_id=str(item["site_id"]),
                title=str(item["title"]),
                description=str(item.get("description", "")),
                spaces=tuple(str(value) for value in item.get("spaces", [])),
                device_ids=frozenset(
                    str(value) for value in item.get("device_ids", [])
                ),
            )
            for item in payload["profiles"]
        }
        configured = os.getenv(
            "WOT_SIM_PROFILE",
            str(payload["default_profile_id"]),
        )
        if configured not in self._scenes:
            raise ValueError(f"unknown simulator profile: {configured}")
        self._active_profile_id = configured
        self._lock = RLock()

    def list(self) -> list[SimulatorScene]:
        return list(self._scenes.values())

    def require(self, profile_id: str) -> SimulatorScene:
        try:
            return self._scenes[profile_id]
        except KeyError as exc:
            raise LookupError(f"unknown simulator profile: {profile_id}") from exc

    def active(self) -> SimulatorScene:
        with self._lock:
            return self._scenes[self._active_profile_id]

    def activate(self, profile_id: str) -> SimulatorScene:
        scene = self.require(profile_id)
        with self._lock:
            self._active_profile_id = profile_id
        return scene

    def allows(self, device_id: str) -> bool:
        return device_id in self.active().device_ids

    def profiles_for_device(self, device_id: str) -> list[str]:
        return sorted(
            scene.profile_id
            for scene in self._scenes.values()
            if device_id in scene.device_ids
        )

    def assert_allowed(self, device_id: str) -> None:
        scene = self.active()
        if device_id not in scene.device_ids:
            raise PermissionError(
                f"device '{device_id}' is outside active profile "
                f"'{scene.profile_id}'"
            )


scene_registry = SimulatorSceneRegistry()


__all__ = ["SimulatorScene", "SimulatorSceneRegistry", "scene_registry"]
