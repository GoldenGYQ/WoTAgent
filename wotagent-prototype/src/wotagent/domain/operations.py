"""Domain-facing contracts for the platform operations console."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class DomainOption(BaseModel):
    value: str
    label: str


class TelemetrySignal(BaseModel):
    key: str
    label: str
    unit: str = ""
    value_type: str = "number"
    default_value: str = ""


class DomainScenario(BaseModel):
    key: str
    label: str


class MonitorRuleDefinition(BaseModel):
    key: str
    label: str
    condition: str
    freshness: str
    default_response: str


class DomainActorDefinition(BaseModel):
    actor_id: str
    label: str
    actor_type: str
    responsibility: str
    capabilities: list[str] = Field(default_factory=list)
    resource_scope: list[str] = Field(default_factory=list)
    delegates_to: list[str] = Field(default_factory=list)
    llm_driven: bool = False


class DomainManifest(BaseModel):
    domain_id: str
    title: str
    description: str
    subject_label: str
    subject_label_plural: str
    telemetry_signals: list[TelemetrySignal] = Field(default_factory=list)
    scenarios: list[DomainScenario] = Field(default_factory=list)
    monitor_rules: list[MonitorRuleDefinition] = Field(default_factory=list)
    actors: list[DomainActorDefinition] = Field(default_factory=list)


class DomainAccessDenied(PermissionError):
    pass


class DomainNotFound(LookupError):
    pass


class DomainPlugin(Protocol):
    def manifest(self) -> DomainManifest: ...

    def list_subjects(self, principal_id: str) -> list[dict[str, Any]]: ...

    def overview(
        self,
        subject_id: str,
        principal_id: str,
    ) -> dict[str, Any]: ...

    async def resources(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]: ...

    def events(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]: ...

    def actions(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]: ...

    def monitor_status(self) -> dict[str, Any]: ...

    async def ingest(
        self,
        subject_id: str,
        principal_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def simulate(
        self,
        subject_id: str,
        principal_id: str,
        scenario: str,
    ) -> dict[str, Any]: ...

    def acknowledge(
        self,
        event_id: str,
        principal_id: str,
    ) -> dict[str, Any]: ...


class DomainPluginRegistry:
    def __init__(self, adapters: list[DomainPlugin]) -> None:
        self._adapters = {
            adapter.manifest().domain_id: adapter
            for adapter in adapters
        }

    def manifests(self) -> list[DomainManifest]:
        return [
            adapter.manifest()
            for adapter in self._adapters.values()
        ]

    def require(self, domain_id: str) -> DomainPlugin:
        try:
            return self._adapters[domain_id]
        except KeyError as exc:
            raise DomainNotFound(
                f"domain '{domain_id}' is not registered"
            ) from exc


__all__ = [
    "DomainAccessDenied",
    "DomainActorDefinition",
    "DomainManifest",
    "DomainNotFound",
    "DomainPlugin",
    "DomainPluginRegistry",
    "DomainScenario",
    "MonitorRuleDefinition",
    "TelemetrySignal",
]
