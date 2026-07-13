"""Deterministic policy checks performed outside the LLM."""

from __future__ import annotations

from typing import Any

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
        self._runtime_devices: dict[str, dict[str, Any]] | None = None

    def replace_capabilities(self, devices: list[dict[str, Any]]) -> None:
        """Use one MCP capability snapshot for deterministic policy validation."""
        self._runtime_devices = {
            str(device["device_id"]): device
            for device in devices
            if device.get("device_id")
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

        device = self._runtime_device(step.device_id)
        if device is None:
            return PolicyDecision(
                effect="deny",
                reason_code="DEVICE_NOT_FOUND",
                reason=f"device '{step.device_id}' is not registered",
            )

        if step.kind == "action":
            input_schema = self._action_schema(device, step.action)
            if input_schema is None:
                return PolicyDecision(
                    effect="deny",
                    reason_code="ACTION_NOT_DECLARED",
                    reason=f"action '{step.action}' is not declared by {step.device_id}",
                )
            try:
                validate(instance=step.input, schema=input_schema)
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

    def _runtime_device(self, device_id: str) -> Any | None:
        if self._runtime_devices is not None:
            return self._runtime_devices.get(device_id)
        return get_device_by_id(device_id)

    @staticmethod
    def _action_schema(device: Any, action_name: str) -> dict[str, Any] | None:
        if isinstance(device, dict):
            declared = next(
                (item for item in device.get("actions", []) if item.get("name") == action_name),
                None,
            )
            return dict(declared.get("input_schema") or {}) if declared else None
        declared = next((item for item in device.actions if item.name == action_name), None)
        return dict(declared.input_schema) if declared else None
