from __future__ import annotations

import pytest

from wotagent.agents import (
    DEFAULT_AGENT_REGISTRY,
    AgentRegistry,
    get_agent_spec,
)


def test_registry_selects_only_profile_enabled_agents() -> None:
    selected = DEFAULT_AGENT_REGISTRY.select([
        "security_agent",
        "environment_agent",
    ])

    assert list(selected) == ["security_agent", "environment_agent"]
    assert "health_agent" not in selected


def test_registry_discovers_device_subgraphs_by_runtime() -> None:
    ids = DEFAULT_AGENT_REGISTRY.ids(runtime="langgraph_subgraph")

    assert "environment_agent" in ids
    assert "security_agent" in ids
    assert "health_agent" not in ids


def test_registry_rejects_unknown_profile_agent() -> None:
    with pytest.raises(LookupError, match="unknown agents"):
        DEFAULT_AGENT_REGISTRY.select(["missing_agent"])


def test_registry_rejects_duplicate_agent_ids() -> None:
    spec = get_agent_spec("security_agent")

    with pytest.raises(ValueError, match="duplicate AgentSpec"):
        AgentRegistry([spec, spec])
