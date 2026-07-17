"""Typed Run/Task contracts for goal-driven multi-agent execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..agents.contracts import AgentFinding


RunStatus = Literal[
    "accepted",
    "running",
    "waiting_confirmation",
    "completed",
    "partial_success",
    "failed",
    "cancelled",
]
TaskStatus = Literal[
    "pending",
    "ready",
    "running",
    "waiting_confirmation",
    "completed",
    "failed",
    "blocked",
    "cancelled",
]
AgentRunStatus = Literal[
    "running",
    "waiting_confirmation",
    "completed",
    "failed",
    "cancelled",
]
ExecutionStrategy = Literal["sequential", "parallel_when_safe"]
ResourceMode = Literal["read", "write"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ResourceClaim(BaseModel):
    key: str = Field(min_length=1)
    mode: ResourceMode


class ProposedTask(BaseModel):
    """Task proposed by the Supervisor before system IDs are assigned."""

    local_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    agent_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_evidence: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list, min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    resource_claims: list[ResourceClaim] = Field(default_factory=list)
    parallel_candidate: bool = True
    priority: int = Field(default=1, ge=1, le=10)


class SupervisorTaskPlan(BaseModel):
    goal: str = Field(min_length=1)
    context_summary: str = ""
    execution_strategy: ExecutionStrategy = "parallel_when_safe"
    tasks: list[ProposedTask] = Field(min_length=1)
    success_criteria: list[str] = Field(default_factory=list, min_length=1)
    constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_local_graph(self) -> "SupervisorTaskPlan":
        ids = [item.local_id for item in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task local_id values must be unique")
        known = set(ids)
        for item in self.tasks:
            if item.local_id in item.depends_on:
                raise ValueError(f"task '{item.local_id}' cannot depend on itself")
            missing = set(item.depends_on).difference(known)
            if missing:
                raise ValueError(
                    f"task '{item.local_id}' has unknown dependencies: "
                    f"{sorted(missing)}"
                )
        _assert_acyclic({item.local_id: item.depends_on for item in self.tasks})
        return self


class SupervisorDecision(BaseModel):
    action: Literal["create_tasks", "finish", "clarify"]
    goal: str = Field(min_length=1)
    context_summary: str = ""
    execution_strategy: ExecutionStrategy = "parallel_when_safe"
    tasks: list[ProposedTask] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    final_response: str = ""
    clarification: str = ""
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action(self) -> "SupervisorDecision":
        if self.action == "create_tasks":
            SupervisorTaskPlan(
                goal=self.goal,
                context_summary=self.context_summary,
                execution_strategy=self.execution_strategy,
                tasks=self.tasks,
                success_criteria=self.success_criteria,
                constraints=self.constraints,
            )
        elif self.action == "finish" and not self.final_response:
            raise ValueError("final_response is required when action=finish")
        elif self.action == "clarify" and not self.clarification:
            raise ValueError("clarification is required when action=clarify")
        return self

    def task_plan(self) -> SupervisorTaskPlan:
        if self.action != "create_tasks":
            raise ValueError(f"decision action is {self.action}, not create_tasks")
        return SupervisorTaskPlan(
            goal=self.goal,
            context_summary=self.context_summary,
            execution_strategy=self.execution_strategy,
            tasks=self.tasks,
            success_criteria=self.success_criteria,
            constraints=self.constraints,
        )


class AgentTask(BaseModel):
    task_id: str
    run_id: str
    parent_task_id: str | None = None
    local_id: str
    agent_id: str
    objective: str
    reason: str
    status: TaskStatus = "pending"
    expected_evidence: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    resource_claims: list[ResourceClaim] = Field(default_factory=list)
    parallel_candidate: bool = True
    priority: int = 1
    finding: AgentFinding | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentRunRecord(BaseModel):
    agent_run_id: str
    run_id: str
    task_id: str
    agent_id: str
    attempt: int = Field(default=1, ge=1)
    status: AgentRunStatus = "running"
    iteration_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class RunRecord(BaseModel):
    run_id: str
    conversation_id: str
    turn_id: str
    root_goal: str
    status: RunStatus = "accepted"
    execution_strategy: ExecutionStrategy = "parallel_when_safe"
    success_criteria: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    final_response: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


def _assert_acyclic(dependencies: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"task dependency cycle detected at '{node}'")
        if node in visited:
            return
        visiting.add(node)
        for dependency in dependencies[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for task_id in dependencies:
        visit(task_id)


__all__ = [
    "AgentRunRecord",
    "AgentRunStatus",
    "AgentTask",
    "ExecutionStrategy",
    "ProposedTask",
    "ResourceClaim",
    "RunRecord",
    "RunStatus",
    "SupervisorDecision",
    "SupervisorTaskPlan",
    "TaskStatus",
    "utc_now",
]
