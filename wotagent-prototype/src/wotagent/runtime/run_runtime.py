"""Production adapters from the platform runtime to Run/Task orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from ..agents import (
    AGENT_SPECS,
    AgentFinding,
    AgentFindingPayload,
    Evidence,
    ToolExecutionResult,
)
from ..auth import DEMO_RESIDENT_ASSIGNMENTS, RelationshipAuthorizationService
from ..domains.eldercare.entity_resolution import ResidentEntityResolver
from ..orchestration import (
    AgentTask,
    PendingConfirmation,
    ResourceScope,
    RunCoordinator,
    RunStore,
    SupervisorDecision,
    TaskFailure,
    TaskPreparationError,
    TaskTarget,
    TaskScheduler,
)
from ..domain.entity_resolution import EntityResolutionError
from ..gateway import deployment_device_scope
from ..prompting import compact_history
from ..telemetry import TraceEvent, extract_usage_with_cost


SUPERVISOR_HISTORY_MESSAGES = 6
SUPERVISOR_HISTORY_CHARACTERS = 400
SUPERVISOR_FINDINGS = 6
SUPERVISOR_EVIDENCE_PER_FINDING = 2
SUPERVISOR_TOOL_CALLS_PER_FINDING = 3


class HealthFindingRepairError(ValueError):
    pass


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
        supervisor=_supervisor_decider(runtime, context, store),
        executors=_specialist_executors(
            runtime,
            context,
            domain_graphs,
            store,
        ),
        resumers={
            agent_id: _device_resumer(runtime, graph, context)
            for agent_id, graph in domain_graphs.items()
        },
        task_preparer=_task_preparer(runtime),
        max_supervisor_iterations=runtime.settings.butler_max_iterations,
    )


def _supervisor_decider(
    runtime: Any,
    base_context: dict[str, Any],
    store: RunStore,
) -> Callable[[dict[str, Any]], Any]:
    async def decide(state: dict[str, Any]) -> SupervisorDecision:
        run_id = str(state["run"]["run_id"])
        iteration = int(state["supervisor_iteration"])
        payload = {
            "user_request": state["message"],
            "conversation_context": _compact_supervisor_context(base_context),
            "completed_findings": _compact_findings(state["findings"]),
            "decision_iteration": state["supervisor_iteration"],
        }
        await store.record_event(
            run_id,
            "model.request.started",
            node="butler_run_supervisor",
            iteration=iteration,
            data={
                "purpose": "supervisor_decision",
                "input": payload,
            },
        )
        started = time.perf_counter()
        try:
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
        except Exception as exc:
            await store.record_event(
                run_id,
                "model.request.failed",
                node="butler_run_supervisor",
                iteration=iteration,
                data={
                    "purpose": "supervisor_decision",
                    "error": str(exc),
                    "duration_ms": (
                        time.perf_counter() - started
                    ) * 1000,
                },
            )
            raise
        messages = result.get("messages", [])
        usage = (
            extract_usage_with_cost(messages[-1])
            if messages
            else {}
        )
        await store.record_event(
            run_id,
            "model.request.completed",
            node="butler_run_supervisor",
            iteration=iteration,
            data={
                "purpose": "supervisor_decision",
                "duration_ms": (time.perf_counter() - started) * 1000,
                "output": _bounded_value(
                    result.get("structured_response")
                ),
                **usage,
            },
        )
        return SupervisorDecision.model_validate(
            result.get("structured_response")
        )

    return decide


def _compact_supervisor_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "principal": {
            "user_id": context.get("user_id", "local-user"),
            "role": context.get("role", "operator"),
        },
        "source": _bounded_value(context.get("source", {})),
        "deployment": _deployment_prompt_context(context),
        "history": compact_history(
            context.get("history", []),
            max_messages=SUPERVISOR_HISTORY_MESSAGES,
            max_characters=SUPERVISOR_HISTORY_CHARACTERS,
        ),
        "runtime": _bounded_value(context.get("runtime", {})),
    }


def _compact_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for finding in findings[-SUPERVISOR_FINDINGS:]:
        compacted.append({
            "agent": finding.get("agent"),
            "objective": _bounded_text(finding.get("objective"), 240),
            "status": finding.get("status"),
            "finding": _bounded_text(finding.get("finding"), 500),
            "confidence": finding.get("confidence"),
            "evidence": [
                {
                    "source": item.get("source"),
                    "value": _value_summary(item.get("value")),
                    "timestamp": item.get("timestamp"),
                    "reliability": item.get("reliability"),
                    "description": _bounded_text(
                        item.get("description"),
                        120,
                    ),
                }
                for item in finding.get("evidence", [])[
                    -SUPERVISOR_EVIDENCE_PER_FINDING:
                ]
                if isinstance(item, dict)
            ],
            "recommended_actions": [
                _bounded_text(item, 120)
                for item in finding.get("recommended_actions", [])[:2]
            ],
            "unresolved_questions": [
                _bounded_text(item, 120)
                for item in finding.get("unresolved_questions", [])[:2]
            ],
            "tool_calls": [
                {
                    "success": item.get("success"),
                    "verified": item.get("verified"),
                    "tool_name": item.get("tool_name"),
                    "error_code": item.get("error_code"),
                    "error_message": _bounded_text(
                        item.get("error_message"),
                        120,
                    ),
                    "retryable": item.get("retryable"),
                }
                for item in finding.get("tool_calls", [])[
                    -SUPERVISOR_TOOL_CALLS_PER_FINDING:
                ]
                if isinstance(item, dict)
            ],
        })
    return compacted


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _deployment_prompt_context(context: dict[str, Any]) -> dict[str, Any]:
    deployment = context.get("deployment", {})
    if not isinstance(deployment, dict):
        return {}
    return {
        key: _bounded_value(deployment.get(key))
        for key in (
            "profile_id",
            "domain_id",
            "site_id",
            "title",
            "description",
            "system_prompt",
            "spaces",
            "enabled_agents",
        )
        if key in deployment
    }


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return _bounded_text(value, 300)
    if isinstance(value, dict):
        return {
            _bounded_text(key, 80): _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:16]
        }
    if isinstance(value, list | tuple):
        return [
            _bounded_value(item, depth=depth + 1)
            for item in value[:8]
        ]
    if isinstance(value, str):
        return value[:500]
    return value


def _value_summary(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    bounded = _bounded_value(value)
    rendered = json.dumps(
        bounded,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if len(rendered) <= 200:
        return bounded
    return f"{rendered[:200]}...[truncated]"


def _specialist_executors(
    runtime: Any,
    context: dict[str, Any],
    domain_graphs: dict[str, Any],
    store: RunStore,
) -> dict[str, Callable[[AgentTask], Any]]:
    executors: dict[str, Callable[[AgentTask], Any]] = {
        "health_agent": _health_executor(runtime, store),
        "care_agent": _care_executor(runtime, context, store),
    }
    for agent_id, graph in domain_graphs.items():
        executors[agent_id] = _device_executor(
            runtime,
            graph,
            context,
            store,
        )
    return executors


def _health_executor(
    runtime: Any,
    store: RunStore | None = None,
) -> Callable[[AgentTask], Any]:
    async def execute(task: AgentTask) -> AgentFindingPayload:
        dependency_context = _dependency_context(task)
        if store is not None:
            await store.record_event(
                task.run_id,
                "model.request.started",
                task_id=task.task_id,
                node=task.agent_id,
                data={
                    "purpose": "health_specialist",
                    "input": {
                        "objective": task.objective,
                        "target": (
                            task.target.model_dump(mode="json")
                            if task.target else None
                        ),
                    },
                },
            )
        started = time.perf_counter()
        with runtime.health_gateway.authorized_scope(task.effective_scope):
            result = await asyncio.wait_for(
                runtime.health_react_agent.ainvoke({
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"原始用户请求（权威范围）："
                            f"{task.source_request or task.objective}\n"
                            f"Supervisor 委派目标（不得扩大原始请求）："
                            f"{task.objective}\n"
                            f"规范居民 ID："
                            f"{task.target.id if task.target else ''}\n"
                            "只收集回答原始用户请求所需的最小证据。"
                            "如果委派目标、期望证据或成功条件加入了原始请求"
                            "没有要求的睡眠、趋势、档案等项目，忽略扩张部分。"
                            f"{dependency_context}"
                        ),
                    }],
                }),
                timeout=runtime.settings.total_timeout_seconds,
            )
        if store is not None:
            await _project_health_messages(
                store,
                task,
                result.get("messages", []),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        structured = result.get("structured_response")
        if structured is not None:
            return AgentFindingPayload.model_validate(structured)
        transcript_finding = _finding_from_health_messages(
            result.get("messages", []),
            objective=task.objective,
        )
        if transcript_finding is not None:
            return transcript_finding
        try:
            repaired = await asyncio.wait_for(
                runtime.health_finding_repair_runnable.ainvoke([
                    {
                        "role": "system",
                        "content": (
                            "把 Health Agent 的执行事实修复为严格的 "
                            "AgentFindingPayload。不得增加未出现的证据，"
                            "不得诊断疾病。工具失败或证据不足时必须如实"
                            "使用 failed 或 needs_more_information。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "source_request": task.source_request,
                                "objective": task.objective,
                                "resident_id": (
                                    task.target.id if task.target else None
                                ),
                                "transcript": _compact_agent_messages(
                                    result.get("messages", [])
                                ),
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    },
                ]),
                timeout=runtime.settings.llm_timeout_seconds,
            )
            return AgentFindingPayload.model_validate(
                _structured_tool_arguments(repaired)
            )
        except Exception as exc:
            raise HealthFindingRepairError(str(exc)) from exc

    return execute


def _finding_from_health_messages(
    messages: list[Any],
    *,
    objective: str,
) -> AgentFindingPayload | None:
    tool_calls: list[ToolExecutionResult] = []
    final_summary = ""
    for message in messages:
        message_type = str(
            getattr(message, "type", None)
            or (
                message.get("role", "")
                if isinstance(message, dict)
                else ""
            )
        )
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        if message_type in {"tool", "ToolMessage"}:
            payload = _strict_json_object(content)
            if payload is None:
                continue
            try:
                tool_calls.append(ToolExecutionResult.model_validate(payload))
            except ValueError:
                continue
        elif message_type in {"ai", "assistant", "AIMessage"}:
            if isinstance(content, str) and content.strip():
                final_summary = content.strip()

    if not tool_calls:
        return None

    successful = [
        item for item in tool_calls
        if item.success and item.verified
    ]
    evidence = [
        Evidence(
            source=item.tool_name,
            value=item.observed_state or item.result,
            reliability=1,
            description=f"{item.tool_name} 返回的已验证健康数据",
        )
        for item in successful
    ]
    complete = bool(successful) and len(successful) == len(tool_calls)
    return AgentFindingPayload(
        status="completed" if complete else "needs_more_information",
        finding=(
            final_summary
            or (
                "已读取并验证回答目标所需的健康数据。"
                if complete
                else "部分健康数据读取失败，当前证据不足。"
            )
        ),
        confidence=0.85 if complete else 0.5,
        evidence=evidence,
        unresolved_questions=[] if complete else [objective],
        tool_calls=tool_calls,
    )


def _strict_json_object(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _structured_tool_arguments(result: Any) -> Any:
    if isinstance(result, AgentFindingPayload | dict):
        return result
    tool_calls = getattr(result, "tool_calls", None) or []
    if not tool_calls:
        raise ValueError("repair model did not emit a tool call")
    first = tool_calls[0]
    if isinstance(first, dict):
        return first.get("args")
    return getattr(first, "args", None)


def _compact_agent_messages(messages: list[Any]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in messages[-10:]:
        message_type = str(
            getattr(message, "type", None)
            or (
                message.get("role", "message")
                if isinstance(message, dict)
                else "message"
            )
        )
        content = (
            message.get("content", "")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        )
        compacted.append({
            "type": message_type,
            "name": (
                message.get("name")
                if isinstance(message, dict)
                else getattr(message, "name", None)
            ),
            "content": _bounded_text(content, 1200),
        })
    return compacted


def _task_preparer(runtime: Any) -> Callable[[AgentTask, str, dict[str, Any]], Any]:
    resolver = ResidentEntityResolver(runtime.eldercare_store)
    authorization = RelationshipAuthorizationService(
        DEMO_RESIDENT_ASSIGNMENTS
    )

    async def prepare(
        task: AgentTask,
        message: str,
        task_context: dict[str, Any],
    ) -> AgentTask:
        deployment = task_context.get("deployment", {})
        task.model_requested_capability = task.requested_capability
        capability = _canonical_task_capability(
            task,
            deployment,
        )
        task.requested_capability = capability
        enabled_agents = set(deployment.get("enabled_agents", []))
        if enabled_agents and task.agent_id not in enabled_agents:
            raise TaskPreparationError(_task_failure(
                task,
                category="resource_scope",
                code="AGENT_OUTSIDE_DEPLOYMENT_PROFILE",
                message=(
                    f"Agent '{task.agent_id}' is not enabled in deployment "
                    f"profile '{deployment.get('profile_id', '')}'"
                ),
                retryable=False,
            ))
        if task.agent_id not in {"health_agent", "care_agent"}:
            if (
                task.principal is None
                or not task.principal.granted_capabilities
            ):
                return task
            if (
                "*" not in task.principal.granted_capabilities
                and capability not in task.principal.granted_capabilities
            ):
                raise TaskPreparationError(_task_failure(
                    task,
                    category="authorization",
                    code="AGENT_CAPABILITY_NOT_DELEGABLE",
                    message=(
                        f"Principal '{task.principal.principal_id}' cannot "
                        f"delegate capability '{capability}' to "
                        f"Agent '{task.agent_id}'."
                    ),
                    retryable=False,
                ))
            scoped_devices = _principal_device_scope(
                runtime,
                task.principal,
                deployment,
            )
            if not scoped_devices:
                raise TaskPreparationError(_task_failure(
                    task,
                    category="resource_scope",
                    code="PRINCIPAL_DEVICE_SCOPE_EMPTY",
                    message="Principal 在当前场景没有可委派的设备资源。",
                    retryable=False,
                ))
            task.effective_scope = ResourceScope(
                resource_ids=[
                    f"device:{device_id}"
                    for device_id in scoped_devices
                ],
                capabilities=[capability],
            )
            return task
        target_type = "resident"
        task.target_type = target_type
        trusted_target = (
            task_context.get("source", {}).get("target")
            if isinstance(task_context.get("source"), dict)
            else None
        )
        if (
            isinstance(trusted_target, dict)
            and trusted_target.get("type") == target_type
            and trusted_target.get("id")
        ):
            task.target = TaskTarget.model_validate(trusted_target)
        else:
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


def _canonical_task_capability(
    task: AgentTask,
    deployment: dict[str, Any],
) -> str:
    spec = AGENT_SPECS[task.agent_id]
    proposed = task.requested_capability.strip()
    if proposed in spec.delegation_capabilities:
        return proposed
    if task.agent_id == "environment_agent":
        return (
            "environment.control"
            if deployment.get("domain_id") == "eldercare"
            else "environment.use"
        )
    if task.agent_id == "security_agent":
        has_write_claim = any(
            claim.mode == "write"
            for claim in task.resource_claims
        )
        return "security.use" if has_write_claim else "security.observe"
    if spec.default_capability:
        return spec.default_capability
    raise TaskPreparationError(_task_failure(
        task,
        category="contract",
        code="AGENT_CAPABILITY_CONTRACT_MISSING",
        message=(
            f"Agent '{task.agent_id}' does not declare a canonical "
            "delegation capability."
        ),
        retryable=False,
    ))


def _principal_device_scope(
    runtime: Any,
    principal: Any,
    deployment: dict[str, Any],
) -> list[str]:
    profile_devices = set(deployment.get("device_ids", []))
    if "*" in principal.resource_ids:
        return sorted(profile_devices)
    site_resource = f"site:{deployment.get('site_id', '')}"
    if site_resource in principal.resource_ids:
        return sorted(profile_devices)
    device_ids = {
        item.removeprefix("device:")
        for item in principal.resource_ids
        if item.startswith("device:")
    }
    for resource_id in principal.resource_ids:
        if not resource_id.startswith("resident:"):
            continue
        resident = runtime.eldercare_store.get_resident(
            resource_id.removeprefix("resident:")
        )
        if resident is None:
            continue
        device_ids.update(filter(None, (
            resident.environment_device_id,
            resident.wristband_device_id,
            resident.mattress_device_id,
            resident.environment_sensor_device_id,
        )))
    return sorted(device_ids.intersection(profile_devices))


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
    store: RunStore | None = None,
) -> Callable[[AgentTask], Any]:
    async def execute(task: AgentTask) -> AgentFinding:
        await _ensure_legacy_trace(runtime, task)
        unsubscribe = _subscribe_trace_projection(
            runtime,
            store,
            task,
        )
        try:
            result = await asyncio.wait_for(
                runtime.eldercare_chat_agent.run(
                    task_id=task.task_id,
                    message=f"{task.objective}{_dependency_context(task)}",
                    history=context.get("history", []),
                    role=context.get("role", "operator"),
                    thinking=context.get("thinking", False),
                    routed_agent="care_agent",
                    resident_id=task.target.id if task.target else None,
                ),
                timeout=runtime.settings.total_timeout_seconds,
            )
        finally:
            unsubscribe()
        return _result_finding(task, result)

    return execute


def _device_executor(
    runtime: Any,
    graph: Any,
    context: dict[str, Any],
    store: RunStore | None = None,
) -> Callable[[AgentTask], Any]:
    async def execute(task: AgentTask) -> AgentFinding:
        await _ensure_legacy_trace(runtime, task)
        unsubscribe = _subscribe_trace_projection(
            runtime,
            store,
            task,
        )
        deployment = context.get("deployment", {})
        device_ids = _execution_device_scope(task, deployment)
        try:
            with deployment_device_scope(
                device_ids
            ):
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
                        "runtime": {
                            **context.get("runtime", {}),
                            "deployment": _deployment_prompt_context(context),
                        },
                        "delegated_agent": task.agent_id,
                        "original_message": task.objective,
                        "execution_mode": "observe_act_loop",
                        "expected_evidence": task.expected_evidence,
                        "success_criteria": task.success_criteria,
                        "dependency_findings": [
                            item.model_dump(mode="json")
                            for item in task.dependency_findings
                        ],
                    },
                    },
                    config={"configurable": {"thread_id": task.task_id}},
                ),
                    timeout=runtime.settings.total_timeout_seconds,
                )
        finally:
            unsubscribe()
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


def _device_resumer(
    runtime: Any,
    graph: Any,
    context: dict[str, Any] | None = None,
) -> Callable[[AgentTask, bool], Any]:
    async def resume(task: AgentTask, approved: bool) -> AgentFinding:
        deployment = (context or {}).get("deployment", {})
        device_ids = _execution_device_scope(task, deployment)
        with deployment_device_scope(device_ids):
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


def _execution_device_scope(
    task: AgentTask,
    deployment: dict[str, Any],
) -> list[str] | None:
    if task.effective_scope is not None:
        scoped = [
            item.removeprefix("device:")
            for item in task.effective_scope.resource_ids
            if item.startswith("device:")
        ]
        if scoped:
            return scoped
    return deployment.get("device_ids")


def _subscribe_trace_projection(
    runtime: Any,
    store: RunStore | None,
    task: AgentTask,
) -> Callable[[], None]:
    subscribe = getattr(runtime.trace_store, "subscribe", None)
    if store is None or not callable(subscribe):
        return lambda: None

    async def project(event: TraceEvent) -> None:
        data = dict(event.data)
        if event.duration_ms is not None:
            data.setdefault("duration_ms", event.duration_ms)
        step_id = data.get("step_id")
        await store.record_event(
            task.run_id,
            event.type,
            task_id=task.task_id,
            parent_task_id=task.parent_task_id,
            iteration=event.iteration,
            tool_call_id=(
                str(step_id)
                if step_id is not None
                and event.type.startswith(("agent.tool", "verification."))
                else None
            ),
            node=event.node,
            data=data,
        )

    return subscribe(task.task_id, project)


async def _project_health_messages(
    store: RunStore,
    task: AgentTask,
    messages: list[Any],
    *,
    duration_ms: float,
) -> None:
    for message in messages:
        message_type = str(
            getattr(message, "type", None)
            or (
                message.get("role", "")
                if isinstance(message, dict)
                else ""
            )
        )
        if message_type in {"ai", "assistant", "AIMessage"}:
            tool_calls = (
                message.get("tool_calls", [])
                if isinstance(message, dict)
                else getattr(message, "tool_calls", [])
            )
            for call in tool_calls or []:
                call_data = call if isinstance(call, dict) else {}
                await store.record_event(
                    task.run_id,
                    "agent.tool.started",
                    task_id=task.task_id,
                    tool_call_id=str(call_data.get("id") or ""),
                    node=task.agent_id,
                    data={
                        "tool": call_data.get("name"),
                        "input": _bounded_value(call_data.get("args")),
                    },
                )
        if message_type not in {"tool", "ToolMessage"}:
            continue
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        payload = _strict_json_object(content) or {
            "result": _bounded_text(content, 1200),
        }
        tool_call_id = (
            message.get("tool_call_id")
            if isinstance(message, dict)
            else getattr(message, "tool_call_id", None)
        )
        await store.record_event(
            task.run_id,
            "agent.tool.completed",
            task_id=task.task_id,
            tool_call_id=str(tool_call_id or ""),
            node=task.agent_id,
            data={
                "tool": payload.get("tool_name"),
                "input": _bounded_value(payload.get("arguments")),
                "output": _bounded_value(payload),
            },
        )
    usage = extract_usage_with_cost(messages[-1]) if messages else {}
    await store.record_event(
        task.run_id,
        "model.request.completed",
        task_id=task.task_id,
        node=task.agent_id,
        data={
            "purpose": "health_specialist",
            "duration_ms": duration_ms,
            **usage,
        },
    )


def _dependency_context(task: AgentTask) -> str:
    if not task.dependency_findings:
        return ""
    return (
        "\n\n上游 Task 已取得以下结构化事实。优先复用这些事实；只有数据不足、"
        "过期或需要验证时才重新查询：\n"
        + json.dumps(
            [
                item.model_dump(mode="json")
                for item in task.dependency_findings
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    )


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
