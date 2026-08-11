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
    DEFAULT_AGENT_REGISTRY,
    AgentFinding,
    AgentFindingPayload,
    Evidence,
    ToolExecutionResult,
)
from ..auth import DEMO_RESIDENT_ASSIGNMENTS, RelationshipAuthorizationService
from ..domain import DeploymentResourceResolver
from ..domain.entity_resolution import EntityResolutionError
from ..domains.eldercare.entity_resolution import ResidentEntityResolver
from ..domains.eldercare.health_freshness import (
    apply_completed_health_freshness_guard,
    has_actionable_health_alert_language,
)
from ..gateway import deployment_device_scope
from ..orchestration import (
    AgentTask,
    PendingConfirmation,
    ResourceScope,
    RunCoordinator,
    RunStore,
    SupervisorDecision,
    TaskFailure,
    TaskPreparationError,
    TaskScheduler,
    TaskTarget,
)
from ..prompting import compact_history
from ..telemetry import TraceEvent, extract_usage_with_cost
from ..wot import load_tds

SUPERVISOR_HISTORY_MESSAGES = 6
SUPERVISOR_HISTORY_CHARACTERS = 400
SUPERVISOR_CONTEXT_SOFT_CHARACTERS = 24000
SUPERVISOR_FINDINGS = 6
SUPERVISOR_EVIDENCE_PER_FINDING = 2
SUPERVISOR_TOOL_CALLS_PER_FINDING = 3
CONDITIONAL_TASK_MARKERS = (
    "如果",
    "若",
    "异常则",
    "否则",
    "仅当",
    "only if",
    "if ",
    "unless",
    "otherwise",
    "do nothing",
)


class HealthFindingRepairError(ValueError):
    pass


def build_run_coordinator(
    runtime: Any,
    store: RunStore,
    context: dict[str, Any],
    domain_graphs: dict[str, Any],
) -> RunCoordinator:
    registry = getattr(runtime, "agent_registry", DEFAULT_AGENT_REGISTRY)
    return RunCoordinator(
        store=store,
        scheduler=TaskScheduler(
            available_agents=set(registry.ids()),
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
        normalized_request = _normalize_supervisor_user_request(
            state["message"],
            base_context,
        )
        conversation_context, context_compacted, context_characters = (
            _supervisor_prompt_context(base_context)
        )
        payload = {
            "user_request": normalized_request,
            "conversation_context": conversation_context,
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
                "raw_user_request": state["message"],
                "context_compacted": context_compacted,
                "context_characters": context_characters,
                "context_soft_limit": SUPERVISOR_CONTEXT_SOFT_CHARACTERS,
            },
            raw_data={"input": _json_safe_value(payload)},
        )
        started = time.perf_counter()
        try:
            enabled_agents = list(
                base_context.get("deployment", {}).get(
                    "enabled_agents",
                    [],
                )
            )
            supervisor_for = getattr(runtime, "supervisor_for", None)
            supervisor = (
                supervisor_for(enabled_agents)
                if callable(supervisor_for)
                else runtime.run_supervisor_agent
            )
            result = await asyncio.wait_for(
                supervisor.ainvoke({
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
                    "error_type": type(exc).__name__,
                    "error_repr": repr(exc),
                    "timeout_seconds": runtime.settings.llm_timeout_seconds,
                    "duration_ms": (
                        time.perf_counter() - started
                    ) * 1000,
                },
            )
            if isinstance(exc, TimeoutError):
                raise TimeoutError(
                    "supervisor model request timed out after "
                    f"{runtime.settings.llm_timeout_seconds:.1f}s"
                ) from exc
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
            raw_data={
                "structured_response": _json_safe_value(
                    result.get("structured_response")
                ),
                "messages": _json_safe_value(messages),
            },
        )
        decision = SupervisorDecision.model_validate(
            result.get("structured_response")
        )
        return _apply_supervisor_runtime_guards(decision)

    return decide


def _normalize_supervisor_user_request(
    message: str,
    base_context: dict[str, Any],
) -> str:
    # Continuity now comes from structured ConversationState plus recent
    # dialogue. Domain-specific text rewriting here would create a second,
    # untrusted entity-resolution path.
    del base_context
    return message


def _compact_supervisor_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "principal": _supervisor_principal_context(context),
        "source": _bounded_value(context.get("source", {})),
        "deployment": _deployment_prompt_context(context),
        "history": compact_history(
            context.get("history", []),
            max_messages=SUPERVISOR_HISTORY_MESSAGES,
            max_characters=SUPERVISOR_HISTORY_CHARACTERS,
        ),
        "runtime": _bounded_value(context.get("runtime", {})),
    }


def _supervisor_prompt_context(
    context: dict[str, Any],
) -> tuple[dict[str, Any], bool, int]:
    full_context = _full_supervisor_context(context)
    serialized_characters = _approx_serialized_characters(full_context)
    if serialized_characters <= SUPERVISOR_CONTEXT_SOFT_CHARACTERS:
        return full_context, False, serialized_characters
    return (
        _compact_supervisor_context(context),
        True,
        serialized_characters,
    )


def _full_supervisor_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "principal": _supervisor_principal_context(context),
        "source": _json_safe_value(context.get("source", {})),
        "deployment": _json_safe_value(context.get("deployment", {})),
        "history": _json_safe_value(context.get("history", [])),
        "runtime": _json_safe_value(context.get("runtime", {})),
    }


def _supervisor_principal_context(context: dict[str, Any]) -> dict[str, Any]:
    principal = context.get("principal")
    if isinstance(principal, dict) and principal:
        payload = _json_safe_value(principal)
        return payload if isinstance(payload, dict) else {}
    return {
        "principal_id": context.get("user_id", "local-user"),
        "roles": [context.get("role", "operator")],
    }


def _apply_supervisor_runtime_guards(
    decision: SupervisorDecision,
) -> SupervisorDecision:
    if decision.action != "create_tasks" or len(decision.tasks) <= 1:
        return decision
    tasks_by_local_id = {item.local_id: item for item in decision.tasks}
    filtered = [
        item
        for item in decision.tasks
        if not _should_defer_first_round_care_task(item, tasks_by_local_id)
    ]
    if not filtered or len(filtered) == len(decision.tasks):
        return decision
    return decision.model_copy(update={"tasks": filtered})


def _should_defer_first_round_care_task(
    task: Any,
    tasks_by_local_id: dict[str, Any],
) -> bool:
    if task.agent_id != "care_agent" or not task.depends_on:
        return False
    upstream_agents = {
        tasks_by_local_id[item].agent_id
        for item in task.depends_on
        if item in tasks_by_local_id
    }
    if "health_agent" not in upstream_agents:
        return False
    return _contains_conditional_language(" ".join([
        task.objective,
        task.reason,
        *task.success_criteria,
    ]))


def _contains_conditional_language(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in text or marker in lowered
        for marker in CONDITIONAL_TASK_MARKERS
    )


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


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe_value(model_dump(mode="json"))
        except (TypeError, ValueError):
            return _json_safe_value(model_dump())
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return str(value)


def _approx_serialized_characters(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


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
            health_input = {
                "objective": task.objective,
                "source_request": task.source_request,
                "target": (
                    task.target.model_dump(mode="json")
                    if task.target else None
                ),
            }
            await store.record_event(
                task.run_id,
                "model.request.started",
                task_id=task.task_id,
                node=task.agent_id,
                data={
                    "purpose": "health_specialist",
                    "target_id": task.target.id if task.target else None,
                },
                raw_data={"input": health_input},
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
            await _project_specialist_messages(
                store,
                task,
                result.get("messages", []),
                purpose="health_specialist",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        structured = result.get("structured_response")
        if structured is not None:
            return apply_completed_health_freshness_guard(
                AgentFindingPayload.model_validate(structured),
                objective=task.objective,
            )
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
                apply_completed_health_freshness_guard(
                    AgentFindingPayload.model_validate(
                        _structured_tool_arguments(repaired)
                    ),
                    objective=task.objective,
                )
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
    payload = AgentFindingPayload(
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
    return apply_completed_health_freshness_guard(
        payload,
        objective=objective,
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
    resource_resolver = DeploymentResourceResolver(load_tds())
    authorization = RelationshipAuthorizationService(
        DEMO_RESIDENT_ASSIGNMENTS
    )
    agent_registry = getattr(
        runtime,
        "agent_registry",
        DEFAULT_AGENT_REGISTRY,
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
            agent_registry,
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
                deployment.get("domain_id") == "eldercare"
                and task.target_mention
            ):
                try:
                    task.target_type = "resident"
                    task.target = resolver.resolve(
                        target_type="resident",
                        mention=task.target_mention,
                        text=f"{message}\n{task.objective}",
                    )
                except EntityResolutionError:
                    # Environment/security style tasks can still run with
                    # principal-scoped devices even when resident mention
                    # resolution is unavailable.
                    pass
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
                resource_resolver=resource_resolver,
            )
            scoped_devices = _narrow_device_scope_from_claims(
                scoped_devices,
                task.resource_claims,
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
    agent_registry: Any,
) -> str:
    spec = agent_registry.require(task.agent_id)
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


def _narrow_device_scope_from_claims(
    authorized_device_ids: list[str],
    resource_claims: list[Any],
) -> list[str]:
    """Use concrete Task claims only to narrow an already authorized scope."""
    authorized = set(authorized_device_ids)
    claimed = {
        str(claim.key).removeprefix("device:")
        for claim in resource_claims
        if str(claim.key).removeprefix("device:") in authorized
    }
    return sorted(claimed) if claimed else sorted(authorized)


def _principal_device_scope(
    runtime: Any,
    principal: Any,
    deployment: dict[str, Any],
    *,
    resource_resolver: DeploymentResourceResolver | None = None,
) -> list[str]:
    resolver = resource_resolver or DeploymentResourceResolver(load_tds())

    def resource_location(resource_id: str) -> str | None:
        if resource_id.startswith("space:"):
            return resource_id.removeprefix("space:")
        if not resource_id.startswith("resident:"):
            return None
        resident = runtime.eldercare_store.get_resident(
            resource_id.removeprefix("resident:")
        )
        return resident.room if resident is not None else None

    return resolver.resolve_device_ids(
        principal=principal,
        profile_device_ids=deployment.get("device_ids", []),
        site_id=str(deployment.get("site_id", "")),
        resource_location=resource_location,
    )


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
        conditional_noop = _conditional_care_noop(task)
        if conditional_noop is not None:
            return conditional_noop
        if store is not None:
            await store.record_event(
                task.run_id,
                "model.request.started",
                task_id=task.task_id,
                node=task.agent_id,
                data={
                    "purpose": "care_specialist",
                    "target_id": task.target.id if task.target else None,
                },
                raw_data={
                    "objective": task.objective,
                    "source_request": task.source_request,
                    "dependency_findings": [
                        item.model_dump(mode="json")
                        for item in task.dependency_findings
                    ],
                },
            )
        started = time.perf_counter()
        with runtime.care_gateway.authorized_scope(task.effective_scope):
            result = await asyncio.wait_for(
                runtime.care_react_agent.ainvoke({
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"Original request: {task.source_request or task.objective}\n"
                            f"Delegated care objective: {task.objective}\n"
                            f"Canonical resident ID: "
                            f"{task.target.id if task.target else ''}"
                            f"{_dependency_context(task)}"
                        ),
                    }],
                }),
                timeout=runtime.settings.total_timeout_seconds,
            )
        if store is not None:
            await _project_specialist_messages(
                store,
                task,
                result.get("messages", []),
                purpose="care_specialist",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        return _attest_care_finding(task, result)

    return execute


def _attest_care_finding(
    task: AgentTask,
    result: dict[str, Any],
) -> AgentFinding:
    payload = result.get("structured_response")
    proposed = (
        AgentFindingPayload.model_validate(payload)
        if payload is not None
        else AgentFindingPayload(
            status="needs_more_information",
            finding="Care Agent did not return a structured result.",
            confidence=0,
        )
    )
    actual_calls = _tool_results_from_messages(result.get("messages", []))
    verified = bool(actual_calls) and all(
        call.success and call.verified for call in actual_calls
    )
    status = "completed" if verified else (
        "failed" if actual_calls else "needs_more_information"
    )
    evidence = [
        Evidence(
            source=call.tool_name,
            value=call.observed_state or call.result,
            reliability=1 if call.verified else 0,
            description="Runtime-attested care tool result.",
        )
        for call in actual_calls
    ]
    return AgentFinding(
        agent=task.agent_id,
        objective=task.objective,
        task_id=task.task_id,
        status=status,
        finding=proposed.finding,
        confidence=proposed.confidence if verified else min(proposed.confidence, 0.5),
        evidence=evidence,
        recommended_actions=proposed.recommended_actions,
        unresolved_questions=([] if verified else task.success_criteria),
        tool_calls=actual_calls,
    )


def _tool_results_from_messages(messages: list[Any]) -> list[ToolExecutionResult]:
    results: list[ToolExecutionResult] = []
    for message in messages:
        message_type = str(
            getattr(message, "type", None)
            or (message.get("role", "") if isinstance(message, dict) else "")
        )
        if message_type not in {"tool", "ToolMessage"}:
            continue
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        payload = _strict_json_object(content)
        if payload is None:
            continue
        try:
            results.append(ToolExecutionResult.model_validate(payload))
        except ValueError:
            continue
    return results


def _conditional_care_noop(task: AgentTask) -> AgentFinding | None:
    if task.agent_id != "care_agent" or not task.dependency_findings:
        return None
    if not _contains_conditional_language(" ".join([
        task.objective,
        task.reason,
        *task.success_criteria,
    ])):
        return None
    health_findings = [
        item for item in task.dependency_findings
        if item.agent_id == "health_agent"
    ]
    if not health_findings:
        return None
    latest = health_findings[-1]
    if latest.status != "completed":
        return None
    if (
        latest.recommended_actions
        or has_actionable_health_alert_language(latest.finding)
    ):
        return None
    return AgentFinding(
        agent=task.agent_id,
        objective=task.objective,
        status="skipped",
        finding="上游健康调查未发现需要通知的异常，本次 Care Task 已安全跳过。",
        confidence=1,
        unresolved_questions=[],
        task_id=task.task_id,
    )
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
        resident_context = _resident_runtime_context(runtime, task)
        if store is not None:
            await store.record_event(
                task.run_id,
                "task.execution.context",
                node=task.agent_id,
                task_id=task.task_id,
                data={
                    "target": (
                        task.target.model_dump(mode="json")
                        if task.target else None
                    ),
                    "target_type": task.target_type,
                    "target_mention": task.target_mention,
                    "effective_scope": (
                        task.effective_scope.model_dump(mode="json")
                        if task.effective_scope else None
                    ),
                    "execution_device_ids": device_ids,
                    "delegated_runtime_context": {
                        **context.get("runtime", {}),
                        "deployment": _deployment_prompt_context(context),
                        "resident_context": resident_context,
                    },
                },
            )
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
                            "resident_context": resident_context,
                            "resolved_target": (
                                task.target.model_dump(mode="json")
                                if task.target else None
                            ),
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


def _resident_runtime_context(
    runtime: Any,
    task: AgentTask,
) -> dict[str, Any] | None:
    if task.target is None or task.target.type != "resident":
        return None
    resident = runtime.eldercare_store.get_resident(task.target.id)
    if resident is None:
        return None
    return {
        "resident_id": resident.resident_id,
        "name": resident.name,
        "room": resident.room,
        "environment_device_id": resident.environment_device_id,
        "environment_sensor_device_id": resident.environment_sensor_device_id,
        "wristband_device_id": resident.wristband_device_id,
        "mattress_device_id": resident.mattress_device_id,
    }


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
            data=_bounded_value(data),
            raw_data=(
                _json_safe_value(
                    event.raw_data
                    if event.raw_data is not None
                    else data
                )
                if event.type.startswith((
                    "model.",
                    "agent.tool",
                    "agent.resource",
                    "verification.",
                ))
                else None
            ),
        )

    return subscribe(task.task_id, project)


async def _project_specialist_messages(
    store: RunStore,
    task: AgentTask,
    messages: list[Any],
    *,
    purpose: str,
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
            "purpose": purpose,
            "duration_ms": duration_ms,
            **usage,
        },
        raw_data={"messages": _json_safe_value(messages)},
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
        "partial_success": "partial_success",
        "responded": "completed",
        "clarify": "needs_more_information",
        "denied": "denied",
        "waiting_confirmation": "blocked",
        "failed": "failed",
        "planning_failed": "failed",
    }.get(raw_status, "failed")
    plan = result.get("plan")
    plan_intent = (
        plan.get("intent")
        if isinstance(plan, dict)
        else getattr(plan, "intent", None)
    )
    # A terminal chat plan after a successful Observe-Act loop is normal: the
    # planner is saying that no further action is required. Only downgrade a
    # chat-only result when the whole graph state contains no executed step.
    if (
        status == "completed"
        and plan_intent in {"chat", "clarify"}
        and not _has_device_execution_evidence(result, trace)
    ):
        status = "needs_more_information"
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


def _has_device_execution_evidence(
    result: dict[str, Any],
    trace: Any,
) -> bool:
    candidates = [trace, *list(result.get("execution_history", []) or [])]
    for candidate in candidates:
        steps = (
            candidate.get("steps", [])
            if isinstance(candidate, dict)
            else getattr(candidate, "steps", [])
        )
        for step in steps or []:
            execution = (
                step.get("execution")
                if isinstance(step, dict)
                else getattr(step, "execution", None)
            )
            verification = (
                step.get("verification")
                if isinstance(step, dict)
                else getattr(step, "verification", None)
            )
            if execution is not None or verification is not None:
                return True
    return False


__all__ = ["build_run_coordinator"]
