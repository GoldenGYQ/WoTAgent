"""Production adapters from the platform runtime to Run/Task orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from ..agents import AGENT_SPECS, AgentFinding, AgentFindingPayload, Evidence
from ..auth import RelationshipAuthorizationService
from ..domains.eldercare.entity_resolution import ResidentEntityResolver
from ..orchestration import (
    AgentTask,
    PendingConfirmation,
    RunCoordinator,
    RunStore,
    SupervisorDecision,
    TaskFailure,
    TaskPreparationError,
    TaskScheduler,
)
from ..domain.entity_resolution import EntityResolutionError


def build_run_coordinator(
    runtime: Any,
    store: RunStore,
    context: dict[str, Any],
    domain_graphs: dict[str, Any],
) -> RunCoordinator:
    return RunCoordinator(
        store=store,
        scheduler=TaskScheduler(
            available_agents=set(AGENT_SPECS),
            max_tasks_per_run=16,
            max_parallel_tasks=4,
        ),
        supervisor=_supervisor_decider(runtime, context),
        executors=_specialist_executors(runtime, context, domain_graphs),
        resumers={
            agent_id: _device_resumer(runtime, graph)
            for agent_id, graph in domain_graphs.items()
        },
        task_preparer=_task_preparer(runtime),
        max_supervisor_iterations=runtime.settings.butler_max_iterations,
    )


def _supervisor_decider(
    runtime: Any,
    base_context: dict[str, Any],
) -> Callable[[dict[str, Any]], Any]:
    async def decide(state: dict[str, Any]) -> SupervisorDecision:
        payload = {
            "user_request": state["message"],
            "conversation_context": base_context,
            "completed_findings": state["findings"],
            "decision_iteration": state["supervisor_iteration"],
        }
        result = await asyncio.wait_for(
            runtime.run_supervisor_agent.ainvoke({
                "messages": [{
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        default=str,
                    ),
                }],
            }),
            timeout=runtime.settings.llm_timeout_seconds,
        )
        return SupervisorDecision.model_validate(
            result.get("structured_response")
        )

    return decide


def _specialist_executors(
    runtime: Any,
    context: dict[str, Any],
    domain_graphs: dict[str, Any],
) -> dict[str, Callable[[AgentTask], Any]]:
    executors: dict[str, Callable[[AgentTask], Any]] = {
        "health_agent": _health_executor(runtime),
        "care_agent": _care_executor(runtime, context),
    }
    for agent_id, graph in domain_graphs.items():
        executors[agent_id] = _device_executor(runtime, graph, context)
    return executors


def _health_executor(runtime: Any) -> Callable[[AgentTask], Any]:
    async def execute(task: AgentTask) -> AgentFindingPayload:
        with runtime.health_gateway.authorized_scope(task.effective_scope):
            result = await asyncio.wait_for(
                runtime.health_react_agent.ainvoke({
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"任务目标：{task.objective}\n"
                            f"规范居民 ID："
                            f"{task.target.id if task.target else ''}\n"
                            f"期望证据：{task.expected_evidence}\n"
                            f"成功条件：{task.success_criteria}"
                        ),
                    }],
                }),
                timeout=runtime.settings.total_timeout_seconds,
            )
        return AgentFindingPayload.model_validate(
            result.get("structured_response")
        )

    return execute


def _task_preparer(runtime: Any) -> Callable[[AgentTask, str, dict[str, Any]], Any]:
    resolver = ResidentEntityResolver(runtime.eldercare_store)
    authorization = RelationshipAuthorizationService({
        "JH-1": {"elderly-001"},
        "JH-2": {"elderly-002"},
        "local-user": {"elderly-001", "elderly-002"},
    })
    default_capabilities = {
        "health_agent": "health.status.read",
        "care_agent": "care.coordinate",
    }

    async def prepare(
        task: AgentTask,
        message: str,
        _context: dict[str, Any],
    ) -> AgentTask:
        capability = (
            task.requested_capability
            or default_capabilities.get(task.agent_id, "")
        )
        task.requested_capability = capability
        if task.agent_id not in {"health_agent", "care_agent"}:
            return task
        target_type = task.target_type or "resident"
        try:
            task.target = resolver.resolve(
                target_type=target_type,
                mention=task.target_mention,
                text=f"{message}\n{task.objective}",
            )
        except EntityResolutionError as exc:
            raise TaskPreparationError(_task_failure(
                task,
                category="entity_resolution",
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            )) from exc
        if task.principal is None:
            raise TaskPreparationError(_task_failure(
                task,
                category="authorization",
                code="PRINCIPAL_MISSING",
                message="Task 缺少经过运行时认证的 Principal。",
                retryable=False,
            ))
        decision = authorization.check(
            principal=task.principal,
            capability=capability,
            target=task.target,
        )
        if decision.effect != "allow" or decision.effective_scope is None:
            raise TaskPreparationError(_task_failure(
                task,
                category="authorization",
                code="RESOURCE_ACCESS_DENIED",
                message=decision.reason,
                retryable=False,
            ))
        task.effective_scope = decision.effective_scope
        return task

    return prepare


def _task_failure(
    task: AgentTask,
    *,
    category: str,
    code: str,
    message: str,
    retryable: bool,
) -> TaskFailure:
    subject = "|".join([
        task.agent_id,
        task.requested_capability,
        task.target.id if task.target else (task.target_mention or ""),
        code,
    ])
    return TaskFailure(
        category=category,
        code=code,
        message=message,
        retryable=retryable,
        fingerprint=hashlib.sha256(subject.encode("utf-8")).hexdigest()[:24],
    )


def _care_executor(
    runtime: Any,
    context: dict[str, Any],
) -> Callable[[AgentTask], Any]:
    async def execute(task: AgentTask) -> AgentFinding:
        await _ensure_legacy_trace(runtime, task)
        result = await asyncio.wait_for(
            runtime.eldercare_chat_agent.run(
                task_id=task.task_id,
                message=task.objective,
                history=context.get("history", []),
                role=context.get("role", "operator"),
                thinking=context.get("thinking", False),
                routed_agent="care_agent",
                resident_id=None,
            ),
            timeout=runtime.settings.total_timeout_seconds,
        )
        return _result_finding(task, result)

    return execute


def _device_executor(
    runtime: Any,
    graph: Any,
    context: dict[str, Any],
) -> Callable[[AgentTask], Any]:
    async def execute(task: AgentTask) -> AgentFinding:
        await _ensure_legacy_trace(runtime, task)
        result = await asyncio.wait_for(
            graph.ainvoke(
                {
                    "request_id": f"req_{uuid4().hex[:12]}",
                    "task_id": task.task_id,
                    "session_id": task.run_id,
                    "user_id": context.get("user_id", "local-user"),
                    "role": context.get("role", "operator"),
                    "message": task.objective,
                    "thinking": context.get("thinking", False),
                    "conversation_history": context.get("history", []),
                    "context": {
                        "runtime": context.get("runtime", {}),
                        "delegated_agent": task.agent_id,
                        "original_message": task.objective,
                        "execution_mode": "observe_act_loop",
                        "expected_evidence": task.expected_evidence,
                        "success_criteria": task.success_criteria,
                    },
                },
                config={"configurable": {"thread_id": task.task_id}},
            ),
            timeout=runtime.settings.total_timeout_seconds,
        )
        interrupts = result.get("__interrupt__", [])
        if interrupts:
            payload = interrupts[0].value
            return PendingConfirmation(
                payload=payload if isinstance(payload, dict) else {
                    "message": str(payload),
                },
            )
        return _result_finding(task, result)

    return execute


def _device_resumer(runtime: Any, graph: Any) -> Callable[[AgentTask, bool], Any]:
    async def resume(task: AgentTask, approved: bool) -> AgentFinding:
        result = await asyncio.wait_for(
            graph.ainvoke(
                Command(resume={"approved": approved}),
                config={
                    "configurable": {
                        "thread_id": task.task_id,
                    }
                },
            ),
            timeout=runtime.settings.total_timeout_seconds,
        )
        return _result_finding(task, result)

    return resume


async def _ensure_legacy_trace(runtime: Any, task: AgentTask) -> None:
    if await runtime.trace_store.get(task.task_id) is None:
        await runtime.trace_store.create(
            task_id=task.task_id,
            session_id=task.run_id,
            request_id=f"req_{uuid4().hex[:12]}",
        )


def _result_finding(task: AgentTask, result: dict[str, Any]) -> AgentFinding:
    trace = result.get("trace")
    trace_status = getattr(getattr(trace, "status", None), "value", None)
    raw_status = str(
        result.get("status")
        or trace_status
        or result.get("phase", "")
    )
    status = {
        "succeeded": "completed",
        "partial_success": "completed",
        "responded": "completed",
        "clarify": "needs_more_information",
        "denied": "blocked",
        "waiting_confirmation": "blocked",
        "failed": "failed",
        "planning_failed": "failed",
    }.get(raw_status, "failed")
    response = str(
        result.get("response")
        or result.get("error")
        or f"{task.agent_id} returned status {raw_status or 'unknown'}"
    )
    evidence: list[Evidence] = []
    if trace is not None:
        trace_data = (
            trace.model_dump(mode="json")
            if hasattr(trace, "model_dump")
            else trace
        )
        evidence.append(Evidence(
            source=f"{task.agent_id}.execution_trace",
            value=trace_data,
            reliability=1 if status == "completed" else 0.5,
            description="专业 Agent 的执行与验证记录",
        ))
    return AgentFinding(
        agent=task.agent_id,
        objective=task.objective,
        status=status,
        finding=response,
        confidence=0.9 if status == "completed" else 0.4,
        evidence=evidence,
        unresolved_questions=(
            task.success_criteria if status != "completed" else []
        ),
    )


__all__ = ["build_run_coordinator"]
