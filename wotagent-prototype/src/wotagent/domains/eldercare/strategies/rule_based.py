"""Original deterministic eldercare strategy, retained as Oracle and fallback."""

from __future__ import annotations

from typing import Any

from ..models import (
    AgentFinding,
    DelegatedAction,
    DelegatedTask,
    Delegation,
    GoalEvaluation,
    GoalPlan,
    HealthAgentDecision,
    RiskEvent,
)


def legacy_delegation_plan(event: RiskEvent) -> list[DelegatedTask]:
    requested = event.context.get("requested_delegations")
    if isinstance(requested, list):
        return [DelegatedTask.model_validate(item) for item in requested]
    resident_id = event.resident_id
    care_actions = [DelegatedAction(
        name="notify_caregiver",
        arguments={
            "resident_id": resident_id,
            "message": event.message,
            "urgency": event.severity,
        },
    )]
    if event.event_type in {
        "high_heart_rate",
        "high_blood_pressure",
        "low_spo2",
        "fall_detected",
    }:
        care_actions.append(DelegatedAction(
            name="create_care_task",
            arguments={
                "resident_id": resident_id,
                "title": f"处置：{event.message}",
                "priority": event.severity,
            },
        ))
    delegations = [DelegatedTask(
        agent_id="care_agent",
        objective=f"通知护理员并记录风险：{event.message}",
        resident_scope=[resident_id],
        allowed_mcp="eldercare-care",
        actions=care_actions,
    )]
    temperature = (
        event.context.get("latest", {})
        .get("room_temperature", {})
        .get("value")
    )
    if event.event_type == "room_overheat" or (
        isinstance(temperature, (int, float)) and float(temperature) > 28
    ):
        delegations.append(DelegatedTask(
            agent_id="environment_agent",
            objective="将老人房间温度调整到舒适范围",
            resident_scope=[resident_id],
            allowed_mcp="eldercare-environment",
            actions=[DelegatedAction(
                name="adjust_temperature",
                arguments={
                    "resident_id": resident_id,
                    "target_temperature": 24.0,
                    "reason": event.message,
                },
            )],
        ))
    return delegations


class RuleBasedGoalPlanner:
    """Create a conservative goal plan without an LLM."""

    async def __call__(
        self,
        event: RiskEvent,
        available_agents: list[str],
    ) -> GoalPlan:
        delegations = [
            Delegation(
                agent="health_agent",
                objective=f"核实并汇总风险事件证据：{event.message}",
                reason="健康风险必须先核实最新数据和数据质量",
                expected_evidence=["最新生命体征", "数据更新时间", "风险趋势"],
                priority=1,
            )
        ]
        if event.event_type == "room_overheat":
            delegations.append(Delegation(
                agent="environment_agent",
                objective="验证并将居民房间温度调节到舒适范围",
                reason="Monitor 已触发房间过热硬规则",
                expected_evidence=["调温后的已验证设备状态"],
                priority=2,
            ))
        success_criteria = [
            "健康风险证据已核实",
            "中高风险事件已通知人工",
        ]
        if event.severity in {"high", "critical"}:
            success_criteria.append("必要的照护任务已创建")
        return GoalPlan(
            goal=f"安全处置 {event.message}",
            context_summary=(
                f"居民 {event.resident_id}，事件 {event.event_type}，"
                f"严重程度 {event.severity}"
            ),
            delegations=[
                item for item in delegations if item.agent in available_agents
            ],
            success_criteria=success_criteria,
            constraints=[
                "不得诊断疾病或调整药物",
                "医疗硬规则不可被 LLM 忽略",
                "设备动作必须经过 Policy、MCP Scope 和状态验证",
            ],
            max_iterations=4,
        )


class RuleBasedGoalEvaluator:
    """Fallback evaluation used when the model is unavailable or invalid."""

    async def __call__(
        self,
        goal_plan: GoalPlan,
        findings: list[AgentFinding],
        event: RiskEvent,
        iteration: int,
        available_agents: list[str],
    ) -> GoalEvaluation:
        health = next((item for item in findings if item.agent == "health_agent"), None)
        care_findings = [item for item in findings if item.agent == "care_agent"]
        care = next(
            (item for item in reversed(care_findings) if item.status == "completed"),
            care_findings[-1] if care_findings else None,
        )
        environment = next(
            (item for item in findings if item.agent == "environment_agent"),
            None,
        )
        next_delegations: list[Delegation] = []
        if (
            health is not None
            and event.event_type != "user_request"
            and (care is None or care.status != "completed")
        ):
            assigned_failed = any(
                item.status == "failed"
                and not any(
                    call.tool_name == "notify_duty_caregiver"
                    for call in item.tool_calls
                )
                for item in care_findings
            )
            next_delegations.append(Delegation(
                agent="care_agent",
                objective=(
                    f"通知当前值班护理员并创建照护任务：{event.message}"
                    if assigned_failed
                    else f"通知人工并创建照护任务：{event.message}"
                    if event.severity in {"high", "critical"}
                    else f"通知护理员关注：{event.message}"
                ),
                reason=(
                    "指定护理员通知失败，使用授权的值班护理员补偿方案"
                    if assigned_failed
                    else "Monitor 硬告警要求风险事件通知人工"
                ),
                expected_evidence=["通知送达结果", "照护任务编号"],
                priority=1,
            ))
        if event.event_type == "room_overheat" and environment is None:
            next_delegations.append(Delegation(
                agent="environment_agent",
                objective="将居民房间温度调节到 24 摄氏度并验证",
                reason="房间过热成功条件尚未满足",
                expected_evidence=["调温后设备状态"],
                priority=2,
            ))
        next_delegations = [
            item for item in next_delegations
            if item.agent in available_agents
        ]
        notified = care is not None and care.status == "completed"
        environment_ok = (
            event.event_type != "room_overheat"
            or (environment is not None and environment.status == "completed")
        )
        health_checked = health is not None and bool(health.evidence)
        completed = health_checked and notified and environment_ok
        satisfied = []
        if health_checked:
            satisfied.append("健康风险证据已核实")
        if notified:
            satisfied.append("中高风险事件已通知人工")
            if "必要的照护任务已创建" in goal_plan.success_criteria:
                satisfied.append("必要的照护任务已创建")
        unsatisfied = [
            criterion for criterion in goal_plan.success_criteria
            if criterion not in satisfied
        ]
        return GoalEvaluation(
            goal_completed=completed,
            satisfied_criteria=satisfied,
            unsatisfied_criteria=unsatisfied,
            next_delegations=[] if completed else next_delegations,
            final_reason=(
                "规则成功条件已满足"
                if completed
                else f"第 {iteration} 轮后仍有 {len(unsatisfied)} 项条件未满足"
            ),
        )


class RuleBasedHealthDecisionMaker:
    """Deterministic Health loop Oracle used only for fallback and tests."""

    async def __call__(self, context: dict[str, Any]) -> HealthAgentDecision:
        evidence = context.get("evidence", [])
        sources = {str(item.get("source", "")) for item in evidence}
        if "read_health_summary" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="read_health_summary",
                reason="先读取聚合健康摘要建立调查基线",
            )
        if "check_data_quality" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="check_data_quality",
                reason="检查摘要中传感器数据是否新鲜",
            )
        quality = _evidence_value(evidence, "check_data_quality")
        sensors = quality.get("sensors", {}) if isinstance(quality, dict) else {}
        stale = any(
            isinstance(item, dict) and not item.get("fresh", False)
            for item in sensors.values()
        )
        if stale and "read_latest_vitals" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="read_latest_vitals",
                reason="摘要包含过期数据，需要主动读取最新生命体征",
            )
        latest = _evidence_value(evidence, "read_latest_vitals")
        summary = _evidence_value(evidence, "read_health_summary")
        abnormal = _abnormal_vitals(latest) or _abnormal_summary(summary)
        if abnormal and "read_vital_trend" not in sources:
            return HealthAgentDecision(
                action="use_tool",
                tool_name="read_vital_trend",
                reason="最新值异常，需要趋势证据判断是否持续",
            )
        recommended = ["通知护理员复核"] if abnormal else []
        return HealthAgentDecision(
            action="finish",
            reason="现有资源已满足有限健康调查要求",
            status="completed",
            finding=(
                "发现需要护理人员复核的异常生命体征。"
                if abnormal
                else "未发现需要升级处置的明确异常，已完成健康信息核实。"
            ),
            confidence=0.85 if evidence else 0.2,
            recommended_actions=recommended,
            unresolved_questions=[],
        )


def _evidence_value(evidence: list[dict[str, Any]], source: str) -> Any:
    item = next((entry for entry in evidence if entry.get("source") == source), {})
    return item.get("value")


def _abnormal_vitals(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    heart = payload.get("heart_rate", {})
    spo2 = payload.get("spo2", {})
    heart_value = heart.get("value") if isinstance(heart, dict) else None
    spo2_value = spo2.get("value") if isinstance(spo2, dict) else None
    return (
        isinstance(heart_value, (int, float)) and float(heart_value) > 100
    ) or (
        isinstance(spo2_value, (int, float)) and float(spo2_value) < 92
    )


def _abnormal_summary(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return _abnormal_vitals(payload.get("latest", {})) or bool(payload.get("open_risks"))


def rule_shadow_signature(event: RiskEvent) -> dict[str, Any]:
    """Return the rule Oracle decision without executing its actions."""
    tasks = legacy_delegation_plan(event)
    return {
        "agents": [item.agent_id for item in tasks],
        "actions": [
            f"{item.agent_id}.{action.name}"
            for item in tasks
            for action in item.actions
        ],
    }


__all__ = [
    "RuleBasedGoalEvaluator",
    "RuleBasedGoalPlanner",
    "RuleBasedHealthDecisionMaker",
    "legacy_delegation_plan",
    "rule_shadow_signature",
]
