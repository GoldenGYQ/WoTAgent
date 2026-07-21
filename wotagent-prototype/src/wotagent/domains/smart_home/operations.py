"""Villa smart-home projection onto the Domain Operations contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ...domain import DeploymentProfile
from ...domain.operations import (
    DomainAccessDenied,
    DomainActorDefinition,
    DomainManifest,
    DomainScenario,
    MonitorRuleDefinition,
    TelemetrySignal,
)
from ...identity import DEFAULT_PRINCIPAL_DIRECTORY, PrincipalDirectory


SITE_RESOURCE_ID = "site:villa-demo"


class SmartHomeDomainPlugin:
    def __init__(
        self,
        *,
        get_gateway: Callable[[], Any],
        get_profile: Callable[[], DeploymentProfile],
        principals: PrincipalDirectory | None = None,
    ) -> None:
        self.get_gateway = get_gateway
        self.get_profile = get_profile
        self.principals = principals or DEFAULT_PRINCIPAL_DIRECTORY
        self._events: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []

    def manifest(self) -> DomainManifest:
        return DomainManifest(
            domain_id="smart_home",
            title="别墅智能管控",
            description="住宅空间、设备、安防、能耗与家务协同领域",
            subject_label="管理范围",
            subject_label_plural="管理范围",
            telemetry_signals=[
                TelemetrySignal(
                    key="smoke_level",
                    label="烟雾浓度",
                    unit="%",
                    default_value="70",
                ),
                TelemetrySignal(
                    key="power_watts",
                    label="实时功率",
                    unit="W",
                    default_value="8500",
                ),
                TelemetrySignal(
                    key="door_locked",
                    label="入口门锁",
                    value_type="boolean",
                    default_value="true",
                ),
            ],
            scenarios=[
                DomainScenario(
                    key="security_patrol",
                    label="启动安防巡查",
                ),
                DomainScenario(
                    key="away_mode",
                    label="执行离家布防",
                ),
                DomainScenario(
                    key="smoke_alarm",
                    label="模拟烟雾告警",
                ),
                DomainScenario(
                    key="energy_peak",
                    label="模拟能耗峰值",
                ),
            ],
            monitor_rules=[
                MonitorRuleDefinition(
                    key="smoke_alarm",
                    label="烟雾告警",
                    condition="smokeLevel >= 60 或 alarm = true",
                    freshness="1 分钟",
                    default_response="生成高风险事件并交由安防 Agent 核查",
                ),
                MonitorRuleDefinition(
                    key="energy_peak",
                    label="能耗峰值",
                    condition="powerWatts > 8000 W",
                    freshness="5 分钟",
                    default_response="生成能耗事件并委派 Energy Agent",
                ),
                MonitorRuleDefinition(
                    key="entrance_unlocked",
                    label="入口未锁",
                    condition="离家模式下 locked = false",
                    freshness="1 分钟",
                    default_response="交由 Security Agent 并要求人工确认敏感动作",
                ),
            ],
            actors=[
                DomainActorDefinition(
                    actor_id="butler_run_supervisor",
                    label="目标编排 Supervisor",
                    actor_type="supervisor",
                    responsibility="理解住户目标、拆分任务并协调专业主体",
                    capabilities=["task.delegate", "finding.aggregate"],
                    resource_scope=["site:villa-demo"],
                    delegates_to=[
                        "security_agent",
                        "environment_agent",
                        "energy_agent",
                        "housekeeping_agent",
                        "device_ops_agent",
                    ],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="security_agent",
                    label="安防处置 Agent",
                    actor_type="specialist_agent",
                    responsibility="核查门锁、摄像头、燃气和烟雾风险",
                    capabilities=["wot.security"],
                    resource_scope=[
                        "entrance",
                        "hall",
                        "kitchen",
                    ],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="environment_agent",
                    label="环境调节 Agent",
                    actor_type="specialist_agent",
                    responsibility="管理灯光、空调、空气和湿度设备",
                    capabilities=["wot.environment"],
                    resource_scope=["site:villa-demo"],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="energy_agent",
                    label="能耗管理 Agent",
                    actor_type="specialist_agent",
                    responsibility="读取能耗并执行受限负载调度",
                    capabilities=["wot.energy"],
                    resource_scope=["utility_room", "garage"],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="housekeeping_agent",
                    label="家务执行 Agent",
                    actor_type="specialist_agent",
                    responsibility="协调清洁和家务设备",
                    capabilities=["wot.housekeeping"],
                    resource_scope=["ground_floor"],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="device_ops_agent",
                    label="设备运维 Agent",
                    actor_type="specialist_agent",
                    responsibility="检查 TD、MCP 接入和设备在线状态",
                    capabilities=["wot.device_ops"],
                    resource_scope=["site:villa-demo"],
                    llm_driven=True,
                ),
                DomainActorDefinition(
                    actor_id="villa-rule-monitor",
                    label="住宅规则监视器",
                    actor_type="deterministic_service",
                    responsibility="对注入数据执行确定性安全与能耗规则",
                    capabilities=["telemetry.observe", "risk.emit"],
                    resource_scope=["site:villa-demo"],
                    llm_driven=False,
                ),
            ],
        )

    def list_subjects(self, principal_id: str) -> list[dict[str, Any]]:
        if not self._allows(principal_id, "domain.read"):
            return []
        return [self._subject(principal_id)]

    def overview(
        self,
        subject_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        self._require(subject_id, principal_id, "domain.read")
        profile = self.get_profile()
        capabilities = self._principal(principal_id).granted_capabilities
        return {
            "subject": self._subject(principal_id),
            "metrics": [
                self._metric("spaces", "空间数量", len(profile.spaces), "个"),
                self._metric(
                    "devices",
                    "接入设备",
                    len(profile.device_ids),
                    "台",
                ),
                self._metric(
                    "actors",
                    "软件主体",
                    len(self.manifest().actors),
                    "个",
                ),
                self._metric(
                    "control",
                    "控制权限",
                    "可控制" if "device.control" in capabilities or "*" in capabilities
                    else "只读",
                    "",
                ),
            ],
            "open_event_count": len([
                item for item in self._events
                if item["status"] == "open"
            ]),
            "generated_at": datetime.now(UTC).isoformat(),
        }

    async def resources(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        self._require(subject_id, principal_id, "device.read")
        profile = self.get_profile()
        catalog = await self.get_gateway().list_devices()
        catalog = [
            item for item in catalog
            if str(item.get("id")) in profile.device_ids
        ]
        states = await asyncio.gather(
            *[
                self._resource(item)
                for item in catalog
            ]
        )
        return sorted(
            states,
            key=lambda item: (
                str(item.get("state", {}).get("location", "")),
                item["resource_id"],
            ),
        )

    def events(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        self._require(subject_id, principal_id, "domain.read")
        return list(reversed(self._events))

    def actions(
        self,
        subject_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        self._require(subject_id, principal_id, "domain.read")
        return list(reversed(self._actions))

    def monitor_status(self) -> dict[str, Any]:
        return {
            "service_id": "villa-rule-monitor",
            "status": "online",
            "reported_status": "deterministic_on_ingest",
            "details": {
                "mode": "deterministic",
                "rules": len(self.manifest().monitor_rules),
                "llm_driven": False,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "age_seconds": 0,
        }

    async def ingest(
        self,
        subject_id: str,
        principal_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require(subject_id, principal_id, "scenario.execute")
        signal = str(payload["signal"])
        value = payload["value"]
        observed_at = datetime.now(UTC).isoformat()
        if signal == "smoke_level":
            result = await self._execute(
                principal_id,
                "smoke-sensor-001",
                "updateSmokeState",
                {
                    "smokeLevel": float(value),
                    "alarm": float(value) >= 60,
                    "observedAt": observed_at,
                },
                "security_agent",
            )
            if float(value) >= 60:
                self._emit_event(
                    "smoke_alarm",
                    "high",
                    f"大厅烟雾浓度达到 {value}%，超过安全阈值。",
                    "smoke-sensor-001",
                )
            return result
        if signal == "power_watts":
            result = await self._execute(
                principal_id,
                "smart-meter-001",
                "updateEnergyState",
                {
                    "powerWatts": float(value),
                    "observedAt": observed_at,
                },
                "energy_agent",
            )
            if float(value) > 8000:
                self._emit_event(
                    "energy_peak",
                    "medium",
                    f"别墅实时功率达到 {value} W。",
                    "smart-meter-001",
                )
            return result
        if signal == "door_locked":
            return await self._execute(
                principal_id,
                "door-lock-001",
                "lock" if bool(value) else "unlock",
                {},
                "security_agent",
            )
        raise ValueError(f"unsupported smart-home signal: {signal}")

    async def simulate(
        self,
        subject_id: str,
        principal_id: str,
        scenario: str,
    ) -> dict[str, Any]:
        self._require(subject_id, principal_id, "scenario.execute")
        if scenario == "security_patrol":
            return await self._execute(
                principal_id,
                "camera-001",
                "startRecording",
                {"durationSeconds": 60},
                "security_agent",
            )
        if scenario == "away_mode":
            results = [
                await self._execute(
                    principal_id,
                    "door-lock-001",
                    "lock",
                    {},
                    "security_agent",
                ),
                await self._execute(
                    principal_id,
                    "camera-001",
                    "startRecording",
                    {"durationSeconds": 60},
                    "security_agent",
                ),
            ]
            return {"scenario": scenario, "results": results}
        if scenario == "smoke_alarm":
            return await self.ingest(
                subject_id,
                principal_id,
                {"signal": "smoke_level", "value": 75},
            )
        if scenario == "energy_peak":
            return await self.ingest(
                subject_id,
                principal_id,
                {"signal": "power_watts", "value": 9200},
            )
        raise ValueError(f"unknown smart-home scenario: {scenario}")

    def acknowledge(
        self,
        event_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        self._require(
            "villa-demo",
            principal_id,
            "event.acknowledge",
        )
        event = next(
            (item for item in self._events if item["event_id"] == event_id),
            None,
        )
        if event is None:
            raise KeyError("smart-home event not found")
        event["status"] = "acknowledged"
        return {
            "event_id": event_id,
            "status": event["status"],
            "run_id": event.get("run_id"),
        }

    async def _resource(
        self,
        capability: dict[str, Any],
    ) -> dict[str, Any]:
        device_id = str(capability.get("id", ""))
        try:
            state = await self.get_gateway().query_state(device_id)
            error = None
            status = "online"
        except Exception as exc:
            state = {}
            error = str(exc)
            status = "offline"
        actions = [
            str(item.get("name"))
            for item in capability.get("actions", [])
        ]
        return {
            "resource_id": device_id,
            "label": capability.get("title") or device_id,
            "resource_type": "device",
            "kind": "actuator" if actions else "sensor",
            "transport": "WoT TD / MCP",
            "status": status,
            "last_seen": state.get("observedAt"),
            "capabilities": actions,
            "state": {
                "location": capability.get("location", ""),
                **state,
            },
            "error": error,
        }

    async def _execute(
        self,
        principal_id: str,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        agent_id: str,
    ) -> dict[str, Any]:
        result = await self.get_gateway().control_device(
            device_id,
            action,
            parameters,
            idempotency_key=f"domain-{uuid4().hex}",
        )
        observed = await self.get_gateway().query_state(device_id)
        verified = bool(result.get("success", True)) and bool(observed)
        record = {
            "action_id": f"action_{uuid4().hex[:12]}",
            "subject_id": "villa-demo",
            # Kept while older clients still read the eldercare-specific field.
            "resident_id": "villa-demo",
            "agent_id": agent_id,
            "action": f"{device_id}.{action}",
            "arguments": parameters,
            "result": {
                **result,
                "observed_state": observed,
                "execution_mode": "wot_mcp",
                "verified": verified,
                "principal_id": principal_id,
            },
            "execution_mode": "wot_mcp",
            "verified": verified,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._actions.append(record)
        return record

    def _emit_event(
        self,
        event_type: str,
        severity: str,
        message: str,
        source: str,
    ) -> dict[str, Any]:
        event = {
            "event_id": f"event_{uuid4().hex[:12]}",
            "subject_id": "villa-demo",
            "event_type": event_type,
            "severity": severity,
            "message": message,
            "status": "open",
            "occurred_at": datetime.now(UTC).isoformat(),
            "source": source,
            "run_id": None,
        }
        self._events.append(event)
        return event

    def _subject(self, principal_id: str) -> dict[str, Any]:
        profile = self.get_profile()
        principal = self._principal(principal_id)
        capabilities = principal.granted_capabilities
        return {
            "subject_id": "villa-demo",
            "label": "云栖别墅",
            "subtitle": (
                f"{len(profile.spaces)} 个空间 · "
                f"{len(profile.device_ids)} 台设备"
            ),
            "metadata": {
                "principal_id": principal_id,
                "capabilities": capabilities,
                "domain_roles": principal.domain_roles,
                "role_labels": principal.role_labels,
                "access": (
                    "operator"
                    if "device.control" in capabilities or "*" in capabilities
                    else "viewer"
                ),
            },
        }

    @staticmethod
    def _metric(
        key: str,
        label: str,
        value: Any,
        unit: str,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "value": value,
            "unit": unit,
            "observed_at": None,
            "source": "deployment-profile",
        }

    def _allows(self, principal_id: str, capability: str) -> bool:
        try:
            return self._principal(principal_id).allows(
                capability,
                SITE_RESOURCE_ID,
            )
        except LookupError:
            return False

    def _principal(self, principal_id: str):
        return self.principals.resolve(principal_id, "smart_home")

    def _require(
        self,
        subject_id: str,
        principal_id: str,
        capability: str,
    ) -> None:
        if subject_id != "villa-demo":
            raise KeyError(f"smart-home subject not found: {subject_id}")
        if not self._allows(principal_id, capability):
            raise DomainAccessDenied(
                f"principal '{principal_id}' lacks '{capability}' "
                "for site 'villa-demo'"
            )


__all__ = ["SmartHomeDomainPlugin"]
