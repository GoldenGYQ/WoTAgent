"""Trusted human/service principal models, separate from software Agent specs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PrincipalType = Literal["human", "service"]


class PrincipalRelationship(BaseModel):
    relation: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    target_label: str | None = None

    @property
    def resource_id(self) -> str:
        return f"{self.target_type}:{self.target_id}"


class DomainRoleBinding(BaseModel):
    domain_id: str = Field(min_length=1)
    platform_role: Literal["viewer", "operator", "admin"]
    roles: list[str] = Field(min_length=1)
    role_labels: list[str] = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    relationships: list[PrincipalRelationship] = Field(default_factory=list)


class PrincipalProfile(BaseModel):
    principal_id: str = Field(min_length=1)
    principal_type: PrincipalType = "human"
    display_name: str = Field(min_length=1)
    login_enabled: bool = False
    bindings: list[DomainRoleBinding] = Field(default_factory=list)


class TaskPrincipal(BaseModel):
    """Runtime-attested user/service identity carried by every Task."""

    principal_id: str = Field(min_length=1)
    principal_type: PrincipalType = "human"
    display_name: str = ""
    domain_id: str = ""
    # Legacy execution role remains explicit while domain roles are adopted.
    roles: list[str] = Field(default_factory=list, min_length=1)
    domain_roles: list[str] = Field(default_factory=list)
    role_labels: list[str] = Field(default_factory=list)
    granted_capabilities: list[str] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    relationships: list[PrincipalRelationship] = Field(default_factory=list)

    def allows(self, capability: str, resource_id: str) -> bool:
        capability_allowed = (
            "*" in self.granted_capabilities
            or capability in self.granted_capabilities
        )
        resource_allowed = (
            "*" in self.resource_ids
            or resource_id in self.resource_ids
        )
        return capability_allowed and resource_allowed


__all__ = [
    "DomainRoleBinding",
    "PrincipalProfile",
    "PrincipalRelationship",
    "PrincipalType",
    "TaskPrincipal",
]
