"""Deployment profiles define one coherent IoT world for a Run."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from ..paths import project_root


class DeploymentProfile(BaseModel):
    profile_id: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    system_prompt: str = Field(min_length=1)
    spaces: list[str] = Field(default_factory=list)
    device_ids: list[str] = Field(default_factory=list)
    enabled_agents: list[str] = Field(default_factory=list)


class DeploymentProfileCatalog(BaseModel):
    default_profile_id: str
    profiles: list[DeploymentProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> DeploymentProfileCatalog:
        ids = [profile.profile_id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("deployment profile IDs must be unique")
        if self.default_profile_id not in ids:
            raise ValueError("default deployment profile does not exist")
        device_owners: dict[str, str] = {}
        for profile in self.profiles:
            for device_id in profile.device_ids:
                owner = device_owners.setdefault(device_id, profile.profile_id)
                if owner != profile.profile_id:
                    raise ValueError(
                        f"device '{device_id}' belongs to both '{owner}' "
                        f"and '{profile.profile_id}'"
                    )
        return self


def deployment_profile_path() -> Path:
    override = os.getenv("WOTAGENT_DEPLOYMENT_PROFILES", "").strip()
    if override:
        return Path(override)
    return project_root().parent / "config" / "deployment_profiles.json"


class DeploymentProfileRegistry:
    def __init__(self, catalog: DeploymentProfileCatalog) -> None:
        self.catalog = catalog
        self._profiles = {
            profile.profile_id: profile
            for profile in catalog.profiles
        }

    @classmethod
    def load(cls, path: str | Path | None = None) -> DeploymentProfileRegistry:
        source = Path(path) if path is not None else deployment_profile_path()
        payload = json.loads(source.read_text(encoding="utf-8"))
        return cls(DeploymentProfileCatalog.model_validate(payload))

    @property
    def default_profile_id(self) -> str:
        configured = os.getenv("WOTAGENT_DEFAULT_PROFILE", "").strip()
        if configured:
            self.require(configured)
            return configured
        return self.catalog.default_profile_id

    def list(self) -> list[DeploymentProfile]:
        return list(self.catalog.profiles)

    def require(self, profile_id: str | None) -> DeploymentProfile:
        resolved = profile_id or self.default_profile_id
        try:
            return self._profiles[resolved]
        except KeyError as exc:
            raise LookupError(
                f"unknown deployment profile: {resolved}"
            ) from exc


__all__ = [
    "DeploymentProfile",
    "DeploymentProfileCatalog",
    "DeploymentProfileRegistry",
    "deployment_profile_path",
]
