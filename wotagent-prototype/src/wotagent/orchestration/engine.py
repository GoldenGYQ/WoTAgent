"""Bounded, auditable plan execution independent from LangChain agents."""

from __future__ import annotations

import asyncio
from typing import Any

from ..domain import (
    ExecutionResult,
    PlanStep,
    StepTrace,
    TaskPlan,
    TaskStatus,
    TaskTrace,
    VerificationResult,
)
from ..gateway import DeviceGateway
from ..policy import PolicyEngine


class TaskOrchestrator:
    def __init__(self, gateway: DeviceGateway, policy: PolicyEngine | None = None) -> None:
        self.gateway = gateway
        self.policy = policy or PolicyEngine()

    async def prepare(self) -> list[dict[str, Any]] | None:
        initialize = getattr(self.gateway, "initialize", None)
        if callable(initialize):
            await initialize()
        capability_provider = getattr(self.gateway, "capabilities", None)
        if not callable(capability_provider):
            return None
        capabilities = await capability_provider()
        self.policy.replace_capabilities(capabilities)
        return capabilities

    async def execute(
        self,
        plan: TaskPlan,
        *,
        role: str,
        confirmed: bool = False,
        task_id: str | None = None,
    ) -> TaskTrace:
        await self.prepare()
        trace = TaskTrace(
            **({"task_id": task_id} if task_id else {}),
            plan=plan,
            role=role,
            status=TaskStatus.EXECUTING,
        )
        completed: dict[str, StepTrace] = {}

        for step in self._topological_steps(plan):
            decision = self.policy.evaluate(role, step)
            step_trace = StepTrace(step=step, policy=decision)
            trace.steps.append(step_trace)

            if decision.effect == "deny":
                step_trace.execution = ExecutionResult(
                    step_id=step.id,
                    status="skipped",
                    error=decision.reason,
                )
                completed[step.id] = step_trace
                continue
            if decision.effect == "confirm" and not confirmed:
                trace.finish(TaskStatus.WAITING_CONFIRMATION)
                return trace
            if any(not completed[dep].succeeded for dep in step.depends_on):
                step_trace.execution = ExecutionResult(
                    step_id=step.id,
                    status="skipped",
                    error="dependency failed",
                )
                completed[step.id] = step_trace
                continue

            step_trace.execution = await self._execute_step(trace.task_id, step)
            if step_trace.execution.status == "accepted" and step.postconditions:
                step_trace.verification = await self._verify(step)
            completed[step.id] = step_trace

        succeeded = sum(item.succeeded for item in trace.steps)
        if succeeded == len(trace.steps):
            trace.finish(TaskStatus.SUCCEEDED)
        elif succeeded:
            trace.finish(TaskStatus.PARTIAL_SUCCESS)
        elif trace.steps and all(item.policy.effect == "deny" for item in trace.steps):
            trace.finish(TaskStatus.DENIED)
        else:
            trace.finish(TaskStatus.FAILED)
        return trace

    async def _execute_step(self, task_id: str, step: PlanStep) -> ExecutionResult:
        attempts = 0
        last_error = ""
        while attempts < step.retry_policy.max_attempts:
            attempts += 1
            try:
                if step.kind == "query":
                    result = await asyncio.wait_for(
                        self.gateway.query_state(step.device_id),
                        timeout=step.timeout_ms / 1000,
                    )
                else:
                    result = await asyncio.wait_for(
                        self.gateway.control_device(
                            step.device_id,
                            step.action,
                            step.input,
                            idempotency_key=f"{task_id}:{step.id}",
                        ),
                        timeout=step.timeout_ms / 1000,
                    )
                if result.get("success", result.get("accepted", True)):
                    return ExecutionResult(
                        step_id=step.id,
                        status="accepted",
                        adapter=self.gateway.name,
                        raw_result=result,
                        attempts=attempts,
                    )
                last_error = str(result.get("error", "device rejected request"))
            except TimeoutError:
                last_error = "device call timed out"
            except Exception as exc:
                last_error = str(exc)
            if attempts < step.retry_policy.max_attempts and step.retry_policy.delay_ms:
                await asyncio.sleep(step.retry_policy.delay_ms / 1000)

        status = "timeout" if "timed out" in last_error else "failed"
        return ExecutionResult(
            step_id=step.id,
            status=status,
            adapter=self.gateway.name,
            attempts=attempts,
            error=last_error,
        )

    async def _verify(self, step: PlanStep) -> VerificationResult:
        observed = await asyncio.wait_for(
            self.gateway.query_state(step.device_id),
            timeout=step.timeout_ms / 1000,
        )
        failed = [
            f"{condition.property} {condition.operator} {condition.value!r}"
            for condition in step.postconditions
            if not condition.evaluate(observed)
        ]
        return VerificationResult(
            step_id=step.id,
            passed=not failed,
            observed_state=observed,
            failed_conditions=failed,
        )

    @staticmethod
    def _topological_steps(plan: TaskPlan) -> list[PlanStep]:
        by_id = {step.id: step for step in plan.steps}
        ordered: list[PlanStep] = []
        visited: set[str] = set()

        def visit(step: PlanStep) -> None:
            if step.id in visited:
                return
            for dependency in step.depends_on:
                visit(by_id[dependency])
            visited.add(step.id)
            ordered.append(step)

        for item in plan.steps:
            visit(item)
        return ordered
