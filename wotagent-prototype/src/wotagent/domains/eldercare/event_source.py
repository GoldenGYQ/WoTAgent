"""Adapter from the eldercare risk outbox to platform domain events."""

from __future__ import annotations

from typing import Any

from ...domain.events import ClaimedDomainEvent, DomainEventEnvelope


class EldercareDomainEventSource:
    def __init__(self, store: Any) -> None:
        self.store = store

    def claim_events(self, *, limit: int = 10) -> list[ClaimedDomainEvent]:
        claimed: list[ClaimedDomainEvent] = []
        for item in self.store.claim_events(limit=limit):
            event = dict(item["payload"])
            resident = self.store.get_resident(event["resident_id"])
            if resident is None:
                self.store.retry_event(
                    item["outbox_id"],
                    f"resident '{event['resident_id']}' not found",
                )
                continue
            risk = self.store.get_risk(event["event_id"])
            source_context: dict[str, object] = {
                "target": {
                    "type": "resident",
                    "id": resident.resident_id,
                    "display_name": resident.name,
                },
                "event": {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "severity": event["severity"],
                    "message": event["message"],
                    "occurred_at": event["occurred_at"],
                },
            }
            goal = (
                f"处理养老照护监测事件：{event['message']}。"
                f"对象：{resident.name}（{resident.resident_id}），"
                f"严重级别：{event['severity']}。"
                "请核查必要证据，并在授权范围内协调处置。"
            )
            claimed.append(ClaimedDomainEvent(
                outbox_id=item["outbox_id"],
                event=DomainEventEnvelope(
                    event_id=event["event_id"],
                    domain_id="eldercare",
                    subject_id=resident.resident_id,
                    event_type=event["event_type"],
                    severity=event["severity"],
                    message=event["message"],
                    source=event.get("source", "eldercare-monitor"),
                    status=event.get("status", "open"),
                    occurred_at=event["occurred_at"],
                    run_goal=goal,
                    source_context=source_context,
                    principal_id="system:eldercare-monitor",
                    role="operator",
                    run_id=risk.run_id if risk is not None else None,
                ),
            ))
        return claimed

    def complete_event(self, outbox_id: str) -> None:
        self.store.complete_event(outbox_id)

    def retry_event(
        self,
        outbox_id: str,
        error: str,
        *,
        delay_seconds: int = 5,
    ) -> None:
        self.store.retry_event(
            outbox_id,
            error,
            delay_seconds=delay_seconds,
        )

    def link_run(self, event_id: str, run_id: str) -> None:
        self.store.link_risk_run(event_id, run_id)


__all__ = ["EldercareDomainEventSource"]
