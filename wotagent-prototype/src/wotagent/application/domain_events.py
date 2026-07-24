"""Application boundary that converts domain events into Agent Runs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from ..domain.events import ClaimedDomainEvent, DomainEventSource
from .run_service import RunService


class DomainEventConsumer:
    def __init__(
        self,
        sources: Sequence[DomainEventSource],
        run_service: RunService,
    ) -> None:
        self.sources = list(sources)
        self.run_service = run_service

    async def consume_once(self, *, limit: int = 10) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        remaining = max(1, limit)
        for source in self.sources:
            if remaining <= 0:
                break
            claims = source.claim_events(limit=remaining)
            remaining -= len(claims)
            for claim in claims:
                result = await self._consume(source, claim)
                if result is not None:
                    results.append(result)
        return results

    async def _consume(
        self,
        source: DomainEventSource,
        claim: ClaimedDomainEvent,
    ) -> dict[str, Any] | None:
        event = claim.event
        try:
            if event.run_id:
                existing = await self.run_service.require_run(event.run_id)
                if existing.status in {
                    "accepted",
                    "running",
                    "waiting_confirmation",
                }:
                    source.retry_event(
                        claim.outbox_id,
                        f"linked Run {existing.run_id} is still active",
                        delay_seconds=30,
                    )
                    return None
                if existing.status == "failed":
                    source.retry_event(
                        claim.outbox_id,
                        existing.final_response
                        or f"linked Run {existing.run_id} failed",
                    )
                    return None
                source.complete_event(claim.outbox_id)
                return {
                    "event_id": event.event_id,
                    "run_id": existing.run_id,
                    "status": existing.status,
                    "domain_id": event.domain_id,
                    "reused": True,
                }
            run = await self.run_service.create_event_run(
                message=event.run_goal,
                domain_id=event.domain_id,
                source_event_id=event.event_id,
                source_context=event.source_context,
                user_id=event.principal_id,
                role=event.role,
                on_created=lambda created: source.link_run(
                    event.event_id,
                    created.run_id,
                ),
            )
            if run.status == "failed":
                raise RuntimeError(
                    run.final_response or "domain event Run failed"
                )
            source.complete_event(claim.outbox_id)
            return {
                "event_id": event.event_id,
                "run_id": run.run_id,
                "status": run.status,
                "domain_id": event.domain_id,
            }
        except Exception as exc:
            source.retry_event(claim.outbox_id, str(exc))
            return None

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
