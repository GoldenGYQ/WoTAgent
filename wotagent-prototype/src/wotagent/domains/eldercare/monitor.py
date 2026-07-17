"""Deterministic scheduled health-risk evaluation; no LLM calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .models import RiskEvent
from .store import EldercareStore


class HealthMonitor:
    def __init__(self, store: EldercareStore) -> None:
        self.store = store

    def evaluate_all(self, *, now: datetime | None = None) -> list[RiskEvent]:
        observed_at = now or datetime.now(UTC)
        events: list[RiskEvent] = []
        for resident in self.store.list_residents():
            events.extend(self.evaluate_resident(resident.resident_id, now=observed_at))
        return events

    def evaluate_resident(self, resident_id: str, *, now: datetime | None = None) -> list[RiskEvent]:
        observed_at = now or datetime.now(UTC)
        latest = self.store.latest(resident_id)
        candidates: list[RiskEvent] = []

        heart_rates = self.store.readings_since(
            resident_id,
            "heart_rate",
            observed_at - timedelta(minutes=5, seconds=5),
        )
        numeric_hr = [item for item in heart_rates if isinstance(item.value, (int, float))]
        if (
            len(numeric_hr) >= 3
            and numeric_hr[-1].observed_at - numeric_hr[0].observed_at >= timedelta(minutes=4)
            and all(float(item.value) > 100 for item in numeric_hr)
        ):
            candidates.append(self._event(
                resident_id,
                "high_heart_rate",
                "high",
                float(numeric_hr[-1].value),
                "心率连续 5 分钟高于 100 次/分",
                observed_at,
                latest,
            ))

        spo2 = self._fresh_value(
            latest, "spo2", observed_at, max_age=timedelta(minutes=5)
        )
        if isinstance(spo2, (int, float)) and float(spo2) < 92:
            candidates.append(self._event(
                resident_id,
                "low_spo2",
                "critical" if float(spo2) < 88 else "high",
                float(spo2),
                "血氧饱和度低于安全阈值",
                observed_at,
                latest,
            ))

        fall = self._fresh_value(
            latest, "fall_detected", observed_at, max_age=timedelta(minutes=1)
        )
        if fall is True:
            candidates.append(self._event(
                resident_id,
                "fall_detected",
                "critical",
                True,
                "手环检测到疑似跌倒",
                observed_at,
                latest,
            ))

        temperature = self._fresh_value(
            latest, "room_temperature", observed_at, max_age=timedelta(minutes=10)
        )
        if isinstance(temperature, (int, float)) and float(temperature) > 28:
            candidates.append(self._event(
                resident_id,
                "room_overheat",
                "medium",
                float(temperature),
                "房间温度高于 28 摄氏度",
                observed_at,
                latest,
            ))

        created: list[RiskEvent] = []
        for event in candidates:
            stored = self.store.create_risk(event)
            if stored is not None:
                created.append(stored)
        return created

    @staticmethod
    def _fresh_value(
        latest: dict[str, Any],
        sensor_type: str,
        now: datetime,
        *,
        max_age: timedelta,
    ) -> Any:
        item = latest.get(sensor_type, {})
        if not isinstance(item, dict) or not item.get("observed_at"):
            return None
        observed_at = datetime.fromisoformat(str(item["observed_at"]))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        age = now - observed_at.astimezone(UTC)
        return item.get("value") if timedelta(0) <= age <= max_age else None

    @staticmethod
    def _event(
        resident_id: str,
        event_type: str,
        severity: str,
        value: float | bool,
        message: str,
        occurred_at: datetime,
        latest: dict[str, Any],
    ) -> RiskEvent:
        return RiskEvent(
            resident_id=resident_id,
            event_type=event_type,
            severity=severity,
            value=value,
            message=message,
            occurred_at=occurred_at,
            context={"latest": latest},
        )
