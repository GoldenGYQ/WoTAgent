"""Validated registry for platform and domain-contributed Agent specs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from .contracts import AgentSpec


class AgentRegistry:
    def __init__(self, specs: Iterable[AgentSpec]) -> None:
        registered: dict[str, AgentSpec] = {}
        for spec in specs:
            if spec.agent_id in registered:
                raise ValueError(f"duplicate AgentSpec: {spec.agent_id}")
            registered[spec.agent_id] = spec
        self._specs = registered

    def require(self, agent_id: str) -> AgentSpec:
        try:
            return self._specs[agent_id]
        except KeyError as exc:
            raise LookupError(f"unknown agent spec: {agent_id}") from exc

    def as_mapping(self) -> Mapping[str, AgentSpec]:
        return MappingProxyType(self._specs)

    def ids(self, *, runtime: str | None = None) -> tuple[str, ...]:
        return tuple(
            agent_id
            for agent_id, spec in self._specs.items()
            if runtime is None or spec.runtime == runtime
        )

    def select(self, agent_ids: Iterable[str]) -> Mapping[str, AgentSpec]:
        selected: dict[str, AgentSpec] = {}
        missing: list[str] = []
        for agent_id in agent_ids:
            spec = self._specs.get(agent_id)
            if spec is None:
                missing.append(agent_id)
            else:
                selected[agent_id] = spec
        if missing:
            raise LookupError(
                f"deployment references unknown agents: {sorted(set(missing))}"
            )
        return MappingProxyType(selected)


__all__ = ["AgentRegistry"]
