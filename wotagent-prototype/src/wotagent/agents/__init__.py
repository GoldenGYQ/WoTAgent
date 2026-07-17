from .contracts import (
    AgentFinding,
    AgentSpec,
    Delegation,
    DelegationRequest,
    Evidence,
    ToolExecutionResult,
)
from .specs import AGENT_SPECS, get_agent_spec


def build_delegation_tool(*args, **kwargs):
    from .factory import build_delegation_tool as factory
    return factory(*args, **kwargs)


def build_specialist_agent(*args, **kwargs):
    from .factory import build_specialist_agent as factory
    return factory(*args, **kwargs)


def build_supervisor_agent(*args, **kwargs):
    from .factory import build_supervisor_agent as factory
    return factory(*args, **kwargs)


def build_health_react_agent(*args, **kwargs):
    from .health import build_health_react_agent as factory
    return factory(*args, **kwargs)


def build_health_tools(*args, **kwargs):
    from .health import build_health_tools as factory
    return factory(*args, **kwargs)


def build_run_supervisor_agent(*args, **kwargs):
    from .supervisor_agent import build_run_supervisor_agent as factory
    return factory(*args, **kwargs)

__all__ = [
    "AGENT_SPECS",
    "AgentFinding",
    "AgentSpec",
    "Delegation",
    "DelegationRequest",
    "Evidence",
    "ToolExecutionResult",
    "build_delegation_tool",
    "build_health_react_agent",
    "build_health_tools",
    "build_run_supervisor_agent",
    "build_specialist_agent",
    "build_supervisor_agent",
    "get_agent_spec",
]
