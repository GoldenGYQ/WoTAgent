from __future__ import annotations

from wotagent.domain import DomainEventEnvelope, SQLiteDomainEventStore


def event() -> DomainEventEnvelope:
    return DomainEventEnvelope(
        event_id="event-villa-1",
        domain_id="smart_home",
        subject_id="villa-demo",
        event_type="energy_peak",
        severity="medium",
        message="别墅实时功率达到 9200 W。",
        source="smart-meter-001",
        run_goal="核查别墅能耗峰值并在必要时处置。",
        source_context={
            "target": {"type": "site", "id": "villa-demo"},
        },
        principal_id="system:villa-monitor",
    )


def test_sqlite_domain_event_store_persists_event_and_outbox(tmp_path) -> None:
    path = tmp_path / "domain-events.db"
    store = SQLiteDomainEventStore(path)

    stored = store.publish(event())
    reopened = SQLiteDomainEventStore(path)
    claimed = reopened.claim_events()

    assert stored.event_id == "event-villa-1"
    assert len(claimed) == 1
    assert claimed[0].event.domain_id == "smart_home"
    assert claimed[0].event.subject_id == "villa-demo"

    reopened.link_run("event-villa-1", "run-villa-1")
    acknowledged = reopened.update_status(
        "event-villa-1",
        "acknowledged",
    )
    reopened.complete_event(claimed[0].outbox_id)

    assert acknowledged.status == "acknowledged"
    assert reopened.get_event("event-villa-1").run_id == "run-villa-1"
    assert reopened.claim_events() == []


def test_domain_event_filters_are_domain_and_subject_scoped(tmp_path) -> None:
    store = SQLiteDomainEventStore(tmp_path / "domain-events.db")
    store.publish(event())
    other = event().model_copy(update={
        "event_id": "event-villa-2",
        "severity": "high",
        "status": "resolved",
    })
    store.publish(other)

    selected = store.list_events(
        domain_id="smart_home",
        subject_id="villa-demo",
        severity="high",
        status="resolved",
    )

    assert [item.event_id for item in selected] == ["event-villa-2"]
