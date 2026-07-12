"""Deterministic policy checks performed outside the LLM."""

from __future__ import annotations

from jsonschema import ValidationError, validate

from ..auth import get_access_control
from ..domain import PlanStep, PolicyDecision
from ..wot import get_device_by_id


class PolicyEngine:
    def __init__(self, high_risk_actions: set[str] | None = None) -> None:
        self._access_control = get_access_control()
        self._high_risk_actions = high_risk_actions or {
            "unlock",
            "openValve",
            "disableAlarm",
        }

    def evaluate(self, role: str, step: PlanStep) -> PolicyDecision:
        resource = "device.read" if step.kind == "query" else "device.control"
        action = "read" if step.kind == "query" else "execute"
        try:
            allowed = self._access_control.check(role, resource, action)
        except ValueError:
            allowed = False
        if not allowed:
            return PolicyDecision(
                effect="deny",
                reason_code="RBAC_ACTION_DENIED",
                reason=f"role '{role}' cannot {action} {resource}",
            )

        td = get_device_by_id(step.device_id)
        if td is None:
            return PolicyDecision(
                effect="deny",
                reason_code="DEVICE_NOT_FOUND",
                reason=f"device '{step.device_id}' is not registered",
            )

        if step.kind == "action":
            declared = next((item for item in td.actions if item.name == step.action), None)
            if declared is None:
                return PolicyDecision(
                    effect="deny",
                    reason_code="ACTION_NOT_DECLARED",
                    reason=f"action '{step.action}' is not declared by {step.device_id}",
                )
            try:
                validate(instance=step.input, schema=declared.input_schema)
            except ValidationError as exc:
                location = ".".join(str(item) for item in exc.absolute_path)
                detail = f" at '{location}'" if location else ""
                return PolicyDecision(
                    effect="deny",
                    reason_code="PARAMETER_INVALID",
                    reason=f"invalid parameters for '{step.action}'{detail}: {exc.message}",
                )

        if step.action in self._high_risk_actions:
            return PolicyDecision(
                effect="confirm",
                reason_code="HIGH_RISK_CONFIRMATION_REQUIRED",
                reason=f"action '{step.action}' requires explicit confirmation",
                risk_level="high",
            )

        return PolicyDecision(
            effect="allow",
            reason_code="POLICY_ALLOWED",
            reason="role, device, and action checks passed",
        )
