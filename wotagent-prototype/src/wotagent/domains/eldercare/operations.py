"""Eldercare projection onto the platform Domain Operations contract."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ...auth import DEMO_RESIDENT_ASSIGNMENTS
from ...domain.operations import (
    DomainAccessDenied,
    DomainActorDefinition,
    DomainManifest,
    DomainScenario,
    MonitorRuleDefinition,
    TelemetrySignal,
)
from .models import SensorReading


class EldercareOperations:
    def __init__(
        self,
        *,
        get_store: Callable[[], Any],
        get_runtime: Callable[[], Any],
        get_gateway: Callable[[], Any],
    ) -> None:
        self.get_store = get_store
        self.get_runtime = get_runtime
        self.get_gateway = get_gateway

    def manifest(self) -> DomainManifest:
        return DomainManifest(
            domain_id="eldercare",
            title="养老照护",
            description="居民健康、环境与照护协同领域",
            subject_label="照护对象",
            subject_label_plural="照护对象",
            telemetry_signals=[
                TelemetrySignal(
                    key="heart_rate",
                    label="心率",
                    unit="bpm",
                    default_value="82",
                ),
                TelemetrySignal(
                    key="spo2",
                    label="血氧",
                    unit="%",
                    default_value="97",
                ),
                TelemetrySignal(
                    key="fall_detected",
                    label="跌倒状态",
                    value_type="boolean",
                    default_value="false",
                ),
                TelemetrySignal(
                    key="bed_occupancy",
                    label="在床状态",
                    value_type="boolean",
                    default_value="false",
                ),
                TelemetrySignal(
                    key="sleep_quality",
                    label="睡眠质量",
                    value_type="string",
                    default_value="good",
                ),
                TelemetrySignal(
                    key="room_temperature",
                    label="室温",
                    unit="C",
                    default_value="24",
                ),
                TelemetrySignal(
                    key="room_humidity",
                    label="湿度",
                    unit="%",
                    default_value="55",
                ),
            ],
            scenarios=[
                DomainScenario(key="normal", label="正常数据"),
                DomainScenario(
                    key="high_heart_rate",
                    label="持续高心率",
                ),
                DomainScenario(key="low_spo2", label="低血氧"),
                DomainScenario(key="fall", label="模拟跌倒"),
                DomainScenario(key="hot_room", label="房间过热"),
            ],
            monitor_rules=[
                MonitorRuleDefinition(
                    key="high_heart_rate",
                    label="持续高心率",
                    condition="连续约 5 分钟 > 100 bpm",
                    freshness="5 分钟",
                    default_response="创建风险事件并交由 Supervisor 协调",
                ),
                MonitorRuleDefinition(
                    key="low_spo2",
                    label="低血氧",
                    condition="SpO2 < 92%",
                    freshness="5 分钟",
                    default_response="创建高风险事件并通知人工",
                ),
                MonitorRuleDefinition(
                    key="fall_detected",
                    label="疑似跌倒",
                    condition="fall_detected = true",
                    freshness="1 分钟",
                    default_response="创建紧急事件并通知人工",
                ),
                MonitorRuleDefinition(
                    key="room_overheat",
                    label="房间过热",
                    condition="室温 > 28 C",
                    freshness="10 分钟",
                    default_response="委派环境与照护 Agent",
                ),
            ],
            actors=[
                DomainActorDefinition(
                    actor_id="butler_run_supervisor",
                    label="照护管家",
                    actor_type="supervisor",
                    responsibility="理解请求、创建专业 Task 并评估照护目标",
                    capabilities=["task.delegate", "finding.aggregate"],
                    resource_scope=["resident:assigned"],
                    delegates_to=[
                        "health_agent",
                        "care_agent",
                        "environment_agent",
                    ],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="health_agent",
                    label="健康监测专员",
                    actor_type="specialist_agent",
                    responsibility="调查生命体征、趋势和数据质量",
                    capabilities=["health.status.read"],
                    resource_scope=["resident:assigned"],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="care_agent",
                    label="护理协调专员",
                    actor_type="specialist_agent",
                    responsibility="通知护理员并创建照护任务",
                    capabilities=["care.coordinate"],
                    resource_scope=["resident:assigned"],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="eldercare-monitor",
                    label="健康规则监视器",
                    actor_type="deterministic_service",
                    responsibility="持续采集数据并按硬规则生成风险事件",
                    capabilities=["telemetry.observe", "risk.emit"],
                    resource_scope=["resident:assigned"],
                    llm_driven=False,
                ),
            ],
        )

    def list_subjects(self, principal_id: str) -> list[dict[str, Any]]:
        allowed = self._allowed_subjects(principal_id)
        return [
            self._subject(resident)
            for resident in self.get_store().list_residents()
            if resident.resident_id in allowed
        ]

    def overview(
        self,
        subject_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        self._require_access(subject_id, principal_id)
        summary = self.get_store().summary(subject_id)
        metric_definitions = (
            ("heart_rate", "当前心率"),
            ("spo2", "血氧"),
            ("sleep_quality", "睡眠"),
            ("room_temperature", "室温"),
        )
        metrics = []
        for key, label in metric_definitions:
            reading = summary.latest.get(key)
            metrics.append({
                "key": key,
                "label": label,
                "value": reading.get("value") if reading else None,
                "unit": reading.get("unit", "") if reading else "",
                "observed_at": (
                    reading.get("observed_at") if reading else None
                ),
                "source": reading.get("source") if reading else None,
            })
        return {
            "subject": self._subject(summary.resident),
            "metrics": metrics,
            "open_event_count": len(summary.open_risks),
            "generated_at": summary.generated_at.isoformat(),
        }

    async def resources(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        self._require_access(subject_id, principal_id)
        resident = self.get_store().get_resident(subject_id)
        mapped = [
            (resident.wristband_device_id, "sensor"),
            (resident.mattress_device_id, "sensor"),
            (resident.environment_sensor_device_id, "sensor"),
            (resident.environment_device_id, "actuator"),
        ]
        gateway = self.get_gateway()
        try:
            catalog = await gateway.list_devices()
        except Exception:
            catalog = []
        by_id = {str(item.get("id")): item for item in catalog}
        resources: list[dict[str, Any]] = []
        for device_id, kind in mapped:
            if not device_id:
                continue
            capability = by_id.get(device_id, {})
            try:
                state = await gateway.query_state(device_id)
                observed_at = (
                    state.get("observedAt")
                    or datetime.now(UTC).isoformat()
                )
                status = _reading_status(str(observed_at))
                error = None
            except Exception as exc:
                state = {}
                observed_at = None
                status = "offline"
                error = str(exc)
            resources.append({
                "resource_id": device_id,
                "label": capability.get("title") or device_id,
                "resource_type": "device",
                "kind": kind,
                "transport": "WoT TD / MCP",
                "status": status,
                "last_seen": observed_at,
                "capabilities": [
                    item.get("name")
                    for item in capability.get("actions", [])
                ],
                "state": state,
                "error": error,
            })
        return resources

    def events(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        self._require_access(subject_id, principal_id)
        return [
            {
                "event_id": item.event_id,
                "subject_id": item.resident_id,
                "event_type": item.event_type,
                "severity": item.severity,
                "message": item.message,
                "status": item.status,
                "occurred_at": item.occurred_at.isoformat(),
                "source": item.source,
                "run_id": item.run_id,
            }
            for item in self.get_store().list_risks(subject_id)
        ]

    def actions(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        self._require_access(subject_id, principal_id)
        return [
            {
                **item,
                "execution_mode": (
                    item["result"].get("execution_mode")
                    or item["result"].get("delivery")
                    or "recorded"
                ),
                "verified": bool(item["result"].get("verified", False)),
            }
            for item in self.get_store().list_actions(subject_id)
        ]

    def monitor_status(self) -> dict[str, Any]:
        return self.get_store().service_status(
            "eldercare-monitor"
        ) or {
            "service_id": "eldercare-monitor",
            "status": "offline",
            "reported_status": "unknown",
            "details": {},
            "updated_at": None,
            "age_seconds": None,
        }

    async def ingest(
        self,
        subject_id: str,
        principal_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_access(subject_id, principal_id)
        reading = SensorReading(
            resident_id=subject_id,
            sensor_type=payload["signal"],
            value=payload["value"],
            unit=str(payload.get("unit", "")),
            source=str(payload.get("source", "domain-console")),
        )
        return await self.get_runtime().publish_reading(reading)

    async def simulate(
        self,
        subject_id: str,
        principal_id: str,
        scenario: str,
    ) -> dict[str, Any]:
        self._require_access(subject_id, principal_id)
        return await self.get_runtime().simulate(scenario, subject_id)

    def acknowledge(
        self,
        event_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        event = next(
            (
                item
                for item in self.get_store().list_risks(limit=500)
                if item.event_id == event_id
            ),
            None,
        )
        if event is None:
            raise KeyError("risk event not found")
        self._require_access(event.resident_id, principal_id)
        handled = self.get_store().handle_risk(event_id)
        if handled is None:
            raise KeyError("risk event not found")
        return {
            "event_id": handled.event_id,
            "status": handled.status,
            "run_id": handled.run_id,
        }

    @staticmethod
    def _subject(resident: Any) -> dict[str, Any]:
        return {
            "subject_id": resident.resident_id,
            "label": resident.name,
            "subtitle": resident.room,
            "metadata": {
                "caregiver": resident.caregiver,
                "active": resident.active,
            },
        }

    @staticmethod
    def _allowed_subjects(principal_id: str) -> set[str]:
        return set(DEMO_RESIDENT_ASSIGNMENTS.get(principal_id, set()))

    def _require_access(
        self,
        subject_id: str,
        principal_id: str,
    ) -> None:
        if subject_id not in self._allowed_subjects(principal_id):
            raise DomainAccessDenied(
                f"principal '{principal_id}' cannot access "
                f"subject '{subject_id}'"
            )


def _reading_status(last_seen: str | None) -> str:
    if not last_seen:
        return "no_data"
    observed_at = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - observed_at).total_seconds()
    return "online" if age_seconds <= 600 else "stale"


__all__ = ["EldercareOperations"]
