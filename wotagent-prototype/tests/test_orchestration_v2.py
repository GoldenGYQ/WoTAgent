"""Behavioral tests for the WoTAgent 2.0 deterministic core."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from wotagent.domain import Condition, PlanStep, RetryPolicy, TaskPlan, TaskStatus
from wotagent.orchestration import TaskOrchestrator


class FakeGateway:
    name = "fake"

    def __init__(self) -> None:
        self.states = {"light-001": {"on": False, "brightness": 0}}
        self.calls: list[tuple[str, str]] = []
        self.failures_remaining = 0

    async def list_devices(self, location: str | None = None) -> list[dict[str, Any]]:
        return [{"id": "light-001", "state": self.states["light-001"]}]

    async def query_state(self, device_id: str) -> dict[str, Any]:
        self.calls.append(("query", device_id))
        return dict(self.states[device_id])

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append(("control", action))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise ConnectionError("temporary failure")
        if action == "turnOn":
            self.states[device_id]["on"] = True
        elif action == "setBrightness":
            self.states[device_id]["brightness"] = parameters["brightness"]
        return {"success": True, "idempotency_key": idempotency_key}


def light_plan() -> TaskPlan:
    return TaskPlan(
        intent="control",
        summary="turn on the living room light and set brightness",
        steps=[
            PlanStep(
                id="s1",
                kind="action",
                device_id="light-001",
                action="turnOn",
                postconditions=[Condition(property="on", value=True)],
            ),
            PlanStep(
                id="s2",
                kind="action",
                device_id="light-001",
                action="setBrightness",
                input={"brightness": 60},
                depends_on=["s1"],
                postconditions=[Condition(property="brightness", value=60)],
            ),
        ],
    )


def test_executes_dependencies_and_verifies_real_state() -> None:
    gateway = FakeGateway()
    trace = asyncio.run(TaskOrchestrator(gateway).execute(light_plan(), role="operator"))

    assert trace.status == TaskStatus.SUCCEEDED
    assert [item.step.id for item in trace.steps] == ["s1", "s2"]
    assert all(item.verification and item.verification.passed for item in trace.steps)
    assert gateway.states["light-001"] == {"on": True, "brightness": 60}


def test_viewer_is_denied_without_device_calls() -> None:
    gateway = FakeGateway()
    trace = asyncio.run(TaskOrchestrator(gateway).execute(light_plan(), role="viewer"))

    assert trace.status == TaskStatus.DENIED
    assert gateway.calls == []
    assert all(item.policy.reason_code == "RBAC_ACTION_DENIED" for item in trace.steps)


def test_retries_transient_gateway_failure_with_a_bound() -> None:
    gateway = FakeGateway()
    gateway.failures_remaining = 1
    plan = TaskPlan(
        intent="control",
        steps=[PlanStep(
            id="retry",
            kind="action",
            device_id="light-001",
            action="turnOn",
            retry_policy=RetryPolicy(max_attempts=2),
            postconditions=[Condition(property="on", value=True)],
        )],
    )

    trace = asyncio.run(TaskOrchestrator(gateway).execute(plan, role="operator"))

    assert trace.status == TaskStatus.SUCCEEDED
    assert trace.steps[0].execution.attempts == 2


def test_reports_verification_failure_not_false_success() -> None:
    class LyingGateway(FakeGateway):
        async def control_device(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("control", "turnOn"))
            return {"success": True}

    trace = asyncio.run(TaskOrchestrator(LyingGateway()).execute(
        TaskPlan(intent="control", steps=[light_plan().steps[0]]),
        role="operator",
    ))

    assert trace.status == TaskStatus.FAILED
    assert trace.steps[0].execution.status == "accepted"
    assert trace.steps[0].verification.passed is False


def test_rejects_cyclic_plan_before_execution() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        TaskPlan(intent="control", steps=[
            PlanStep(id="a", kind="query", device_id="light-001", action="queryState", depends_on=["b"]),
            PlanStep(id="b", kind="query", device_id="light-001", action="queryState", depends_on=["a"]),
        ])
