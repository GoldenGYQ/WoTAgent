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

from .deployment import (
    DeploymentProfile,
    DeploymentProfileCatalog,
    DeploymentProfileRegistry,
)
from .operations import (
    DomainAccessDenied,
    DomainActorDefinition,
    DomainManifest,
    DomainNotFound,
    DomainPluginRegistry,
)
from .resources import DeploymentResourceResolver, ResourceLocationResolver

__all__ = [
    "Condition",
    "DeploymentProfile",
    "DeploymentProfileCatalog",
    "DeploymentProfileRegistry",
    "DomainAccessDenied",
    "DomainActorDefinition",
    "DomainManifest",
    "DomainNotFound",
    "DomainPluginRegistry",
    "DeploymentResourceResolver",
    "ExecutionResult",
    "PlanStep",
    "PolicyDecision",
    "RetryPolicy",
    "ResourceLocationResolver",
    "StepTrace",
    "TaskPlan",
    "TaskStatus",
    "TaskTrace",
    "VerificationResult",
]
