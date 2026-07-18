"""Framework-independent domain models for the WoTAgent 2.0 task loop."""

from .task import (
    Condition,
    ExecutionResult,
    PlanStep,
    PolicyDecision,
    RetryPolicy,
    StepTrace,
    TaskPlan,
    TaskStatus,
    TaskTrace,
    VerificationResult,
)

__all__ = [
    "Condition",
    "ExecutionResult",
    "PlanStep",
    "PolicyDecision",
    "RetryPolicy",
    "StepTrace",
    "TaskPlan",
    "TaskStatus",
    "TaskTrace",
    "VerificationResult",
]
from .operations import (
    DomainAccessDenied,
    DomainManifest,
    DomainNotFound,
    DomainOperationsRegistry,
)

__all__ = [
    "DomainAccessDenied",
    "DomainManifest",
    "DomainNotFound",
    "DomainOperationsRegistry",
]
