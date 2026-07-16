"""Goal-driven Butler graph with bounded evidence-based re-delegation."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agentic_config import AgentDecisionMode, AgenticSettings
from ..telemetry import InMemoryTraceStore
from .actions import EldercareActionService
from .health_agent import HealthAgentLoop
from .models import (
    AgentFinding,
    Delegation,
    Evidence,
    GoalEvaluation,
    GoalPlan,
    RiskEvent,
    ToolExecutionResult,
)
from .strategies.rule_based import (
    RuleBasedGoalEvaluator,
    RuleBasedGoalPlanner,
)


GoalPlanner = Callable[
    [RiskEvent, list[str]],
    GoalPlan | Awaitable[GoalPlan],
]
GoalEvaluator = Callable[
    [GoalPlan, list[AgentFinding], RiskEvent, int, list[str]],
    GoalEvaluation | Awaitable[GoalEvaluation],
]

AVAILABLE_AGENTS = ["health_agent", "care_agent", "environment_agent"]


class GoalDrivenState(TypedDict, total=False):
    task_id: str
    user_role: str
    event: dict[str, Any]
    decision_mode: AgentDecisionMode
    dry_run: bool
    goal_plan: dict[str, Any]
    pending_delegations: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    evaluation: dict[str, Any]
    iteration: int
    seen_delegations: list[str]
    fallback_reasons: list[str]
    status: str
    response: str


def build_goal_driven_butler_graph(
    action_service: EldercareActionService,
    health_agent: HealthAgentLoop,
    *,
    settings: AgenticSettings,
    trace_store: InMemoryTraceStore | None = None,
    goal_planner: GoalPlanner | None = None,
    goal_evaluator: GoalEvaluator | None = None,
    checkpointer: Any = None,
):
    rule_planner = RuleBasedGoalPlanner()
    rule_evaluator = RuleBasedGoalEvaluator()

    async def plan_goal(state: GoalDrivenState) -> dict[str, Any]:
        event = _event_with_task(state)
        mode = state.get("decision_mode", settings.decision_mode)
        fallback_reasons = list(state.get("fallback_reasons", []))
        planner = rule_planner if mode == "rule" or goal_planner is None else goal_planner
        try:
            plan = await _resolve(planner(event, AVAILABLE_AGENTS))
            plan = GoalPlan.model_validate(plan)
        except Exception as exc:
            fallback_reasons.append(f"goal_planner: {exc}")
            plan = await rule_planner(event, AVAILABLE_AGENTS)
            await _record(trace_store, state, "agent.fallback.activated", "butler", {
                "component": "goal_planner",
                "reason": str(exc),
                "fallback": "rule_based",
            })
        plan.max_iterations = min(
            plan.max_iterations,
            settings.butler_max_iterations,
        )
        if event.event_type != "user_request" and not any(
            item.agent == "health_agent" for item in plan.delegations
        ):
            plan.delegations.insert(0, Delegation(
                agent="health_agent",
                objective=f"调查并核实风险事件：{event.message}",
                reason="健康风险事件必须先获得当前健康证据",
                expected_evidence=["最新生命体征", "数据新鲜度"],
                priority=1,
            ))
        await _record(trace_store, state, "agent.goal.created", "butler", {
            "mode": mode,
            "goal_plan": plan.model_dump(mode="json"),
            "source": event.source,
        })
        await _record(trace_store, state, "agent.delegation.created", "butler", {
            "event_id": event.event_id,
            "goal": plan.goal,
            "delegations": [
                item.model_dump(mode="json") for item in plan.delegations
            ],
        })
        return {
            "goal_plan": plan.model_dump(mode="json"),
            "pending_delegations": [
                item.model_dump(mode="json") for item in plan.delegations
            ],
            "findings": [],
            "iteration": 0,
            "seen_delegations": [],
            "fallback_reasons": fallback_reasons,
            "status": "goal_planned",
        }

    async def dispatch(state: GoalDrivenState) -> dict[str, Any]:
        event = RiskEvent.model_validate(state["event"])
        findings = [
            AgentFinding.model_validate(item)
            for item in state.get("findings", [])
        ]
        seen = set(state.get("seen_delegations", []))
        iteration = state.get("iteration", 0) + 1
        for raw in sorted(
            state.get("pending_delegations", []),
            key=lambda item: int(item.get("priority", 1)),
        ):
            delegation = Delegation.model_validate(raw)
            signature = _delegation_signature(delegation)
            if signature in seen:
                findings.append(_blocked_finding(
                    delegation,
                    "检测到重复委派，已阻止再次执行。",
                ))
                continue
            seen.add(signature)
            await _record(
                trace_store,
                state,
                "agent.delegation.started",
                delegation.agent,
                {
                    "objective": delegation.objective,
                    "expected_evidence": delegation.expected_evidence,
                    "reason": delegation.reason,
                },
                iteration=iteration,
            )
            finding = await _execute_delegation(
                action_service,
                health_agent,
                event,
                delegation,
                task_id=state.get("task_id", ""),
                user_role=state.get("user_role", "system"),
                dry_run=bool(state.get("dry_run", False)),
                max_compensations=settings.max_compensations,
            )
            findings.append(finding)
            for tool_call in finding.tool_calls:
                if delegation.agent == "health_agent":
                    # The Health loop records each MCP Resource call itself.
                    continue
                await _record(
                    trace_store,
                    state,
                    "agent.tool.completed",
                    delegation.agent,
                    {
                        "tool": tool_call.tool_name,
                        "input": tool_call.arguments,
                        "output": tool_call.model_dump(mode="json"),
                    },
                    iteration=iteration,
                )
            await _record(
                trace_store,
                state,
                "agent.finding.completed",
                delegation.agent,
                {"finding": finding.model_dump(mode="json")},
                iteration=iteration,
            )
            await _record(
                trace_store,
                state,
                "agent.subgraph.completed",
                delegation.agent,
                {"result": finding.model_dump(mode="json")},
                iteration=iteration,
            )
        await _record(
            trace_store,
            state,
            "agent.loop.completed",
            "butler",
            {"phase": "dispatch", "finding_count": len(findings)},
            iteration=iteration,
        )
        return {
            "findings": [item.model_dump(mode="json") for item in findings],
            "pending_delegations": [],
            "seen_delegations": sorted(seen),
            "iteration": iteration,
            "status": "observed",
        }

    async def evaluate_goal(state: GoalDrivenState) -> dict[str, Any]:
        event = _event_with_task(state)
        plan = GoalPlan.model_validate(state["goal_plan"])
        findings = [
            AgentFinding.model_validate(item)
            for item in state.get("findings", [])
        ]
        iteration = state.get("iteration", 1)
        mode = state.get("decision_mode", settings.decision_mode)
        fallback_reasons = list(state.get("fallback_reasons", []))
        evaluator = (
            rule_evaluator
            if mode == "rule" or goal_evaluator is None
            else goal_evaluator
        )
        try:
            evaluation = await _resolve(evaluator(
                plan,
                findings,
                event,
                iteration,
                AVAILABLE_AGENTS,
            ))
            evaluation = GoalEvaluation.model_validate(evaluation)
        except Exception as exc:
            fallback_reasons.append(f"goal_evaluator: {exc}")
            evaluation = await rule_evaluator(
                plan,
                findings,
                event,
                iteration,
                AVAILABLE_AGENTS,
            )
            await _record(trace_store, state, "agent.fallback.activated", "butler", {
                "component": "goal_evaluator",
                "reason": str(exc),
                "fallback": "rule_based",
            })
        evaluation = _enforce_hard_health_rules(evaluation, event, findings)
        evaluation = _limit_compensations(
            evaluation,
            findings,
            settings.max_compensations,
        )
        reached_limit = iteration >= plan.max_iterations
        repeated = all(
            _delegation_signature(item) in set(state.get("seen_delegations", []))
            for item in evaluation.next_delegations
        ) if evaluation.next_delegations else False
        if reached_limit and not evaluation.goal_completed:
            evaluation.next_delegations = []
            evaluation.final_reason += "；已达到 Butler 最大迭代次数"
        elif repeated and not evaluation.goal_completed:
            evaluation.next_delegations = []
            evaluation.final_reason += "；后续委派与历史重复，已安全终止"
        await _record(
            trace_store,
            state,
            "agent.goal.evaluated",
            "butler",
            {
                "evaluation": evaluation.model_dump(mode="json"),
                "iteration": iteration,
                "reached_limit": reached_limit,
                "repeated_plan": repeated,
            },
            iteration=iteration,
        )
        return {
            "evaluation": evaluation.model_dump(mode="json"),
            "pending_delegations": [
                item.model_dump(mode="json")
                for item in evaluation.next_delegations
            ],
            "fallback_reasons": fallback_reasons,
            "status": (
                "goal_completed"
                if evaluation.goal_completed
                else "needs_next_iteration"
                if evaluation.next_delegations
                else "goal_incomplete"
            ),
        }

    async def finalize(state: GoalDrivenState) -> dict[str, Any]:
        evaluation = GoalEvaluation.model_validate(state["evaluation"])
        findings = [
            AgentFinding.model_validate(item)
            for item in state.get("findings", [])
        ]
        completed_findings = sum(item.status == "completed" for item in findings)
        if evaluation.goal_completed:
            status = "succeeded"
        elif completed_findings:
            status = "partial_success"
        else:
            status = "failed"
        response = (
            f"目标评估：{evaluation.final_reason}。"
            f" 已满足 {len(evaluation.satisfied_criteria)} 项，"
            f"未满足 {len(evaluation.unsatisfied_criteria)} 项。"
        )
        await _record(trace_store, state, f"task.{status}", "butler", {
            "goal_completed": evaluation.goal_completed,
            "satisfied_criteria": evaluation.satisfied_criteria,
            "unsatisfied_criteria": evaluation.unsatisfied_criteria,
            "fallback_reasons": state.get("fallback_reasons", []),
        })
        return {"status": status, "response": response}

    def after_evaluation(state: GoalDrivenState) -> str:
        if state.get("status") == "needs_next_iteration":
            return "dispatch"
        return "finalize"

    builder = StateGraph(GoalDrivenState)
    builder.add_node("plan_goal", plan_goal)
    builder.add_node("dispatch", dispatch)
    builder.add_node("evaluate_goal", evaluate_goal)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "plan_goal")
    builder.add_edge("plan_goal", "dispatch")
    builder.add_edge("dispatch", "evaluate_goal")
    builder.add_conditional_edges(
        "evaluate_goal",
        after_evaluation,
        {"dispatch": "dispatch", "finalize": "finalize"},
    )
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


async def _execute_delegation(
    actions: EldercareActionService,
    health_agent: HealthAgentLoop,
    event: RiskEvent,
    delegation: Delegation,
    *,
    task_id: str,
    user_role: str,
    dry_run: bool,
    max_compensations: int,
) -> AgentFinding:
    if delegation.agent not in AVAILABLE_AGENTS:
        return _blocked_finding(delegation, "AgentSpec 未注册该专业 Agent。")
    if delegation.agent in {"care_agent", "environment_agent"} and user_role == "viewer":
        return _blocked_finding(delegation, "viewer 角色不能执行养老处置动作。")
    if dry_run:
        return AgentFinding(
            agent=delegation.agent,
            objective=delegation.objective,
            status="needs_more_information",
            finding="Shadow Mode 仅生成委派，不执行资源或设备调用。",
            confidence=0,
            evidence=[],
            recommended_actions=[delegation.objective],
            unresolved_questions=["需要在非 Shadow 模式执行后验证"],
            tool_calls=[],
        )
    if delegation.agent == "health_agent":
        return await health_agent.run(
            task_id=task_id,
            resident_id=event.resident_id,
            delegation=delegation,
        )
    if delegation.agent == "care_agent":
        return await _run_care_agent(
            actions,
            event,
            delegation,
            max_compensations=max_compensations,
        )
    return await _run_environment_agent(actions, event, delegation)


async def _run_care_agent(
    actions: EldercareActionService,
    event: RiskEvent,
    delegation: Delegation,
    *,
    max_compensations: int,
) -> AgentFinding:
    calls: list[ToolExecutionResult] = []
    use_duty = "值班" in delegation.objective
    notify_name = "notify_duty_caregiver" if use_duty else "notify_caregiver"
    notify_handler = (
        actions.notify_duty_caregiver
        if use_duty
        else actions.notify_caregiver
    )
    notify_arguments = {
        "resident_id": event.resident_id,
        "message": event.message,
        "urgency": event.severity,
    }
    try:
        notify = await notify_handler(
            event.resident_id,
            event.message,
            event.severity,
        )
        calls.append(_tool_result(notify_name, notify_arguments, notify))
    except Exception as exc:
        calls.append(_failed_tool_result(notify_name, notify_arguments, exc))
    task_required = (
        event.severity in {"high", "critical"}
        or "任务" in delegation.objective
    )
    if task_required and calls[-1].success and calls[-1].verified:
        task_arguments = {
            "resident_id": event.resident_id,
            "title": f"处置：{event.message}",
            "priority": event.severity,
        }
        try:
            task = await actions.create_care_task(
                event.resident_id,
                f"处置：{event.message}",
                event.severity,
            )
            calls.append(_tool_result("create_care_task", task_arguments, task))
        except Exception as exc:
            calls.append(_failed_tool_result("create_care_task", task_arguments, exc))
    notification_ok = any(
        item.tool_name in {"notify_caregiver", "notify_duty_caregiver"}
        and item.success and item.verified
        for item in calls
    )
    task_ok = (
        not task_required
        or any(
            item.tool_name == "create_care_task" and item.success and item.verified
            for item in calls
        )
    )
    completed = notification_ok and task_ok
    return AgentFinding(
        agent="care_agent",
        objective=delegation.objective,
        status="completed" if completed else "failed",
        finding=(
            "护理通知和照护任务已完成。"
            if completed
            else "护理协同未完全完成，需要人工介入。"
        ),
        confidence=1.0 if completed else 0.3,
        evidence=[
            Evidence(
                source=item.tool_name,
                value=item.result,
                reliability=1.0 if item.verified else 0.0,
                description=(
                    f"{item.tool_name} 已验证"
                    if item.verified
                    else f"{item.tool_name} 未验证或失败"
                ),
            )
            for item in calls
        ],
        recommended_actions=(
            []
            if completed
            else ["通知当前值班护理员"]
            if not use_duty and max_compensations
            else ["人工介入处理通知失败"]
        ),
        unresolved_questions=[] if completed else ["通知是否被人工接收"],
        tool_calls=calls,
    )


async def _run_environment_agent(
    actions: EldercareActionService,
    event: RiskEvent,
    delegation: Delegation,
) -> AgentFinding:
    arguments = {
        "resident_id": event.resident_id,
        "target_temperature": 24.0,
        "reason": delegation.reason,
    }
    try:
        raw = await actions.adjust_temperature(
            event.resident_id,
            24.0,
            delegation.reason,
        )
        result = _tool_result("adjust_temperature", arguments, raw)
    except Exception as exc:
        raw = {"success": False, "verified": False, "error": str(exc)}
        result = _failed_tool_result("adjust_temperature", arguments, exc)
    completed = result.success and result.verified
    return AgentFinding(
        agent="environment_agent",
        objective=delegation.objective,
        status="completed" if completed else "failed",
        finding=(
            "房间温度已调节并通过设备状态验证。"
            if completed
            else "调温调用未通过执行后状态验证。"
        ),
        confidence=1.0 if completed else 0.2,
        evidence=[Evidence(
            source="adjust_temperature",
            value=raw,
            reliability=1.0 if completed else 0.0,
            description="调温后的设备状态验证结果",
        )],
        recommended_actions=[] if completed else ["人工检查空调和房间温度"],
        unresolved_questions=[] if completed else ["设备为何未达到目标温度"],
        tool_calls=[result],
    )


def _tool_result(
    tool_name: str,
    arguments: dict[str, Any],
    raw: dict[str, Any],
) -> ToolExecutionResult:
    success = bool(raw.get("success", False))
    verified = bool(raw.get("verified", success and raw.get("delivery") == "simulated"))
    return ToolExecutionResult(
        success=success,
        verified=verified,
        tool_name=tool_name,
        arguments=arguments,
        result=raw,
        observed_state=raw.get("verified_state"),
        error_code=None if success else str(raw.get("error_code", "TOOL_FAILED")),
        error_message=None if success else str(raw.get("error", "tool execution failed")),
        retryable=bool(raw.get("retryable", False)),
    )


def _failed_tool_result(
    tool_name: str,
    arguments: dict[str, Any],
    exc: Exception,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        success=False,
        verified=False,
        tool_name=tool_name,
        arguments=arguments,
        error_code="TOOL_EXCEPTION",
        error_message=str(exc),
        retryable=False,
    )


def _enforce_hard_health_rules(
    evaluation: GoalEvaluation,
    event: RiskEvent,
    findings: list[AgentFinding],
) -> GoalEvaluation:
    if event.severity not in {"high", "critical"}:
        return evaluation
    care_complete = any(
        item.agent == "care_agent" and item.status == "completed"
        for item in findings
    )
    if care_complete:
        return evaluation
    evaluation.goal_completed = False
    criterion = "中高风险事件已通知人工"
    if criterion not in evaluation.unsatisfied_criteria:
        evaluation.unsatisfied_criteria.append(criterion)
    if not any(item.agent == "care_agent" for item in evaluation.next_delegations):
        assigned_failed = any(
            item.agent == "care_agent"
            and item.status == "failed"
            and not any(
                call.tool_name == "notify_duty_caregiver"
                for call in item.tool_calls
            )
            for item in findings
        )
        evaluation.next_delegations.append(Delegation(
            agent="care_agent",
            objective=(
                f"通知当前值班护理员并创建照护任务：{event.message}"
                if assigned_failed
                else f"通知人工并创建照护任务：{event.message}"
            ),
            reason=(
                "指定护理员通知失败，选择值班护理员补偿方案"
                if assigned_failed
                else "医疗硬规则要求中高风险事件必须通知人工"
            ),
            expected_evidence=["已验证的通知结果", "照护任务编号"],
            priority=1,
        ))
    evaluation.final_reason += "；医疗硬规则尚未满足"
    return evaluation


def _limit_compensations(
    evaluation: GoalEvaluation,
    findings: list[AgentFinding],
    max_compensations: int,
) -> GoalEvaluation:
    used = sum(
        item.agent == "care_agent"
        and any(
            call.tool_name == "notify_duty_caregiver"
            for call in item.tool_calls
        )
        for item in findings
    )
    if used < max_compensations:
        return evaluation
    retained = [
        item for item in evaluation.next_delegations
        if not (
            item.agent == "care_agent"
            and "值班" in item.objective
        )
    ]
    if len(retained) != len(evaluation.next_delegations):
        evaluation.next_delegations = retained
        evaluation.final_reason += "；已达到护理通知补偿上限"
    return evaluation


def _blocked_finding(delegation: Delegation, reason: str) -> AgentFinding:
    return AgentFinding(
        agent=delegation.agent,
        objective=delegation.objective,
        status="blocked",
        finding=reason,
        confidence=1.0,
        evidence=[],
        recommended_actions=[],
        unresolved_questions=[reason],
        tool_calls=[],
    )


def _delegation_signature(delegation: Delegation) -> str:
    return json.dumps(
        {
            "agent": delegation.agent,
            "objective": delegation.objective.strip().lower(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _event_with_task(state: GoalDrivenState) -> RiskEvent:
    event = RiskEvent.model_validate(state["event"])
    event.context = {
        **event.context,
        "task_id": state.get("task_id", ""),
    }
    return event


async def _resolve(value: Any) -> Any:
    return await value if hasattr(value, "__await__") else value


async def _record(
    trace_store: InMemoryTraceStore | None,
    state: GoalDrivenState,
    event_type: str,
    node: str,
    data: dict[str, Any],
    *,
    iteration: int = 0,
) -> None:
    task_id = state.get("task_id", "")
    if trace_store is None or not task_id:
        return
    await trace_store.record(
        task_id=task_id,
        type=event_type,
        node=node,
        data=data,
        iteration=iteration,
    )


__all__ = ["AVAILABLE_AGENTS", "build_goal_driven_butler_graph"]
