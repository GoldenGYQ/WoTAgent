"""Single-point tests for eldercare sensor persistence and risk evaluation."""

from datetime import UTC, datetime, timedelta

from wotagent.domains.eldercare import EldercareStore, HealthMonitor, Resident, SensorReading


def make_store(tmp_path) -> EldercareStore:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(resident_id="elderly-001", name="张爷爷", room="A-101"))
    return store


def test_high_heart_rate_requires_a_sustained_five_minute_window(tmp_path) -> None:
    store = make_store(tmp_path)
    monitor = HealthMonitor(store)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    for minutes, value in [(5, 103), (3, 105), (1, 106), (0, 104)]:
        store.add_reading(SensorReading(
            resident_id="elderly-001",
            sensor_type="heart_rate",
            value=value,
            unit="bpm",
            observed_at=now - timedelta(minutes=minutes),
        ))

    events = monitor.evaluate_resident("elderly-001", now=now)

    assert [event.event_type for event in events] == ["high_heart_rate"]
    assert events[0].severity == "high"


def test_short_heart_rate_spike_does_not_create_an_event(tmp_path) -> None:
    store = make_store(tmp_path)
    monitor = HealthMonitor(store)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    for seconds in (40, 20, 0):
        store.add_reading(SensorReading(
            resident_id="elderly-001",
            sensor_type="heart_rate",
            value=108,
            observed_at=now - timedelta(seconds=seconds),
        ))

    assert monitor.evaluate_resident("elderly-001", now=now) == []


def test_monitor_emits_immediate_fall_and_deduplicates_repeated_scan(tmp_path) -> None:
    store = make_store(tmp_path)
    monitor = HealthMonitor(store)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    store.add_reading(SensorReading(
        resident_id="elderly-001",
        sensor_type="fall_detected",
        value=True,
        observed_at=now,
    ))

    first = monitor.evaluate_resident("elderly-001", now=now)
    second = monitor.evaluate_resident("elderly-001", now=now + timedelta(seconds=30))

    assert first[0].event_type == "fall_detected"
    assert first[0].severity == "critical"
    assert second == []


def test_health_summary_uses_latest_resources_and_24h_average(tmp_path) -> None:
    store = make_store(tmp_path)
    now = datetime.now(UTC)
    for value in (70, 80, 90):
        store.add_reading(SensorReading(
            resident_id="elderly-001",
            sensor_type="heart_rate",
            value=value,
            unit="bpm",
            observed_at=now,
        ))
    store.add_reading(SensorReading(
        resident_id="elderly-001",
        sensor_type="sleep_quality",
        value="poor",
        observed_at=now,
    ))

    summary = store.summary("elderly-001")

    assert summary.heart_rate_avg == 80.0
    assert summary.latest["sleep_quality"]["value"] == "poor"


def test_stale_fall_and_environment_readings_do_not_retrigger(tmp_path) -> None:
    store = make_store(tmp_path)
    now = datetime.now(UTC)
    store.add_reading(SensorReading(
        resident_id="elderly-001",
        sensor_type="fall_detected",
        value=True,
        observed_at=now - timedelta(minutes=2),
    ))
    store.add_reading(SensorReading(
        resident_id="elderly-001",
        sensor_type="room_temperature",
        value=31,
        unit="C",
        observed_at=now - timedelta(minutes=11),
    ))

    events = HealthMonitor(store).evaluate_resident("elderly-001", now=now)

    assert events == []
