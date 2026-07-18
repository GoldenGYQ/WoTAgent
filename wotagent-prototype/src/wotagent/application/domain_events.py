"""Application boundary that converts durable domain events into Agent Runs."""

from __future__ import annotations

import asyncio
from typing import Any

from .run_service import RunService


class DomainEventConsumer:
    def __init__(self, store: Any, run_service: RunService) -> None:
        self.store = store
        self.run_service = run_service

    async def consume_once(self, *, limit: int = 10) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in self.store.claim_events(limit=limit):
            try:
                event = dict(item["payload"])
                resident = self.store.get_resident(event["resident_id"])
                if resident is None:
                    raise KeyError(
                        f"resident '{event['resident_id']}' not found"
                    )
                risk = self.store.get_risk(event["event_id"])
                if risk is not None and risk.run_id:
                    existing = await self.run_service.require_run(risk.run_id)
                    if existing.status in {
                        "accepted",
                        "running",
                        "waiting_confirmation",
                    }:
                        self.store.retry_event(
                            item["outbox_id"],
                            f"linked Run {existing.run_id} is still active",
                            delay_seconds=30,
                        )
                    elif existing.status == "failed":
                        self.store.retry_event(
                            item["outbox_id"],
                            existing.final_response
                            or f"linked Run {existing.run_id} failed",
                        )
                    else:
                        self.store.complete_event(item["outbox_id"])
                        results.append({
                            "event_id": event["event_id"],
                            "run_id": existing.run_id,
                            "status": existing.status,
                            "reused": True,
                        })
                    continue
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
                message = (
                    f"处理养老照护监测事件：{event['message']}。"
                    f"对象：{resident.name}（{resident.resident_id}），"
                    f"严重级别：{event['severity']}。"
                    "请核查必要证据，并在授权范围内协调处置。"
                )
                run = await self.run_service.create_event_run(
                    message=message,
                    domain_id="eldercare",
                    source_event_id=event["event_id"],
                    source_context=source_context,
                    user_id="system:eldercare-monitor",
                    role="operator",
                    on_created=lambda created: self.store.link_risk_run(
                        event["event_id"],
                        created.run_id,
                    ),
                )
                if run.status == "failed":
                    raise RuntimeError(
                        run.final_response or "domain event Run failed"
                    )
                self.store.complete_event(item["outbox_id"])
                results.append({
                    "event_id": event["event_id"],
                    "run_id": run.run_id,
                    "status": run.status,
                })
            except Exception as exc:
                self.store.retry_event(item["outbox_id"], str(exc))
        return results

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        while not stop_event.is_set():
            await self.consume_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=interval_seconds,
                )
            except TimeoutError:
                pass


__all__ = ["DomainEventConsumer"]
