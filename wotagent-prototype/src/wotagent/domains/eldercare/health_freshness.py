"""Shared freshness guards for eldercare health findings."""

from __future__ import annotations

from typing import Any

from ...agents.contracts import AgentFindingPayload, Evidence, ToolExecutionResult

CRITICAL_HEALTH_SENSORS = frozenset({"heart_rate", "spo2", "fall_detected"})
ALERT_KEYWORDS = (
    "异常",
    "告警",
    "风险",
    "复核",
    "通知",
    "跌倒",
    "低血氧",
    "偏低",
    "偏高",
    "abnormal",
    "alert",
    "notify",
    "urgent",
)
NEGATED_ALERT_PATTERNS = (
    "未见明确异常",
    "未见异常",
    "未发现异常",
    "未发现需要",
    "无需通知",
    "无需升级",
    "无异常",
    "正常",
    "no abnormal",
    "no alert",
    "no need to notify",
    "not abnormal",
)
STALE_REVIEW_ACTION = "补采最新生命体征或由护理人员现场复核"


def stale_critical_sensors(
    *,
    tool_calls: list[ToolExecutionResult],
    evidence: list[Evidence],
) -> list[str]:
    stale: set[str] = set()
    for payload in _freshness_payloads(tool_calls=tool_calls, evidence=evidence):
        sensors = payload.get("sensors")
        if not isinstance(sensors, dict):
            continue
        for sensor_name, item in sensors.items():
            if sensor_name not in CRITICAL_HEALTH_SENSORS:
                continue
            if isinstance(item, dict) and item.get("fresh") is False:
                stale.add(sensor_name)
    return sorted(stale)


def should_downgrade_completed_health_finding(
    payload: AgentFindingPayload,
) -> tuple[bool, list[str]]:
    stale = stale_critical_sensors(
        tool_calls=payload.tool_calls,
        evidence=payload.evidence,
    )
    if not stale or payload.status != "completed":
        return False, stale
    if (
        payload.recommended_actions
        or has_actionable_health_alert_language(payload.finding)
    ):
        return False, stale
    return True, stale


def apply_completed_health_freshness_guard(
    payload: AgentFindingPayload,
    *,
    objective: str,
) -> AgentFindingPayload:
    should_downgrade, stale = should_downgrade_completed_health_finding(payload)
    if not should_downgrade:
        return payload
    unresolved = list(payload.unresolved_questions)
    if objective not in unresolved:
        unresolved.append(objective)
    recommended = list(payload.recommended_actions)
    if STALE_REVIEW_ACTION not in recommended:
        recommended.append(STALE_REVIEW_ACTION)
    note = (
        "关键健康数据已过期（"
        + "、".join(stale)
        + "），当前只能说明最近一次记录未见明确异常，"
        "无法充分确认当前状态。"
    )
    finding = payload.finding.strip()
    if note not in finding:
        finding = f"{finding} {note}".strip()
    return payload.model_copy(update={
        "status": "needs_more_information",
        "confidence": min(payload.confidence, 0.45),
        "finding": finding,
        "recommended_actions": recommended,
        "unresolved_questions": unresolved,
    })


def _freshness_payloads(
    *,
    tool_calls: list[ToolExecutionResult],
    evidence: list[Evidence],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in tool_calls:
        if item.tool_name != "check_data_quality":
            continue
        payload = item.observed_state or item.result
        if isinstance(payload, dict):
            payloads.append(payload)
    for item in evidence:
        if item.source != "check_data_quality":
            continue
        if isinstance(item.value, dict):
            payloads.append(item.value)
    return payloads


def has_actionable_health_alert_language(text: str) -> bool:
    lowered = text.lower()
    if any(pattern in text or pattern in lowered for pattern in NEGATED_ALERT_PATTERNS):
        return False
    return any(keyword in text or keyword in lowered for keyword in ALERT_KEYWORDS)


__all__ = [
    "apply_completed_health_freshness_guard",
    "has_actionable_health_alert_language",
    "stale_critical_sensors",
    "should_downgrade_completed_health_finding",
]
