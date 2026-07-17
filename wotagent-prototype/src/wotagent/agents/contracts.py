"""Platform-level contracts shared by supervisors and specialist agents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentStatus = Literal[
    "completed",
    "needs_more_information",
    "blocked",
    "failed",
]


class AgentSpec(BaseModel):
    agent_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    runtime: Literal["eldercare_subgraph", "langgraph_subgraph"]
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    mcp_scopes: frozenset[str] = Field(default_factory=frozenset)
    max_iterations: int = Field(default=4, ge=1, le=8)


class DelegationRequest(BaseModel):
    objective: str = Field(min_length=1)
    expected_evidence: list[str] = Field(default_factory=list)


class Delegation(BaseModel):
    agent: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_evidence: list[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=1, le=10)


class Evidence(BaseModel):
    source: str = Field(min_length=1)
    value: Any | None = None
    timestamp: str | None = None
    reliability: float | None = Field(default=None, ge=0, le=1)
    description: str = Field(min_length=1)


class ToolExecutionResult(BaseModel):
    success: bool
    verified: bool
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None
    observed_state: Any | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class AgentFinding(BaseModel):
    agent: str
    objective: str
    status: AgentStatus
    finding: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    tool_calls: list[ToolExecutionResult] = Field(default_factory=list)


__all__ = [
    "AgentFinding",
    "AgentSpec",
    "AgentStatus",
    "Delegation",
    "DelegationRequest",
    "Evidence",
    "ToolExecutionResult",
]
