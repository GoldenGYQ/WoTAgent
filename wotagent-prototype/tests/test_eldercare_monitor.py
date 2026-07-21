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


def test_sustained_high_blood_pressure_is_classified_by_latest_value(tmp_path) -> None:
    store = make_store(tmp_path)
    monitor = HealthMonitor(store)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    for minutes, value in [(8, "154/96"), (5, "158/98"), (2, "162/101"), (0, "160/99")]:
        store.add_reading(SensorReading(
            resident_id="elderly-001",
            sensor_type="blood_pressure",
            value=value,
            unit="mmHg",
            observed_at=now - timedelta(minutes=minutes),
        ))

    events = monitor.evaluate_resident("elderly-001", now=now)

    assert [(event.event_type, event.severity) for event in events] == [
        ("high_blood_pressure", "high")
    ]
    assert events[0].value == "160/99"


def test_extreme_sustained_blood_pressure_is_critical(tmp_path) -> None:
    store = make_store(tmp_path)
    monitor = HealthMonitor(store)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    for minutes, value in [(7, "170/108"), (4, "176/114"), (0, "181/121")]:
        store.add_reading(SensorReading(
            resident_id="elderly-001",
            sensor_type="blood_pressure",
            value=value,
            observed_at=now - timedelta(minutes=minutes),
        ))

    events = monitor.evaluate_resident("elderly-001", now=now)

    assert events[0].severity == "critical"


def test_fresh_out_of_range_humidity_creates_medium_risk(tmp_path) -> None:
    store = make_store(tmp_path)
    monitor = HealthMonitor(store)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    store.add_reading(SensorReading(
        resident_id="elderly-001",
        sensor_type="room_humidity",
        value=28,
        unit="%",
        observed_at=now,
    ))

    events = monitor.evaluate_resident("elderly-001", now=now)

    assert [(event.event_type, event.severity) for event in events] == [
        ("room_humidity_out_of_range", "medium")
    ]


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
    store.add_reading(SensorReading(
        resident_id="elderly-001",
        sensor_type="room_humidity",
        value=20,
        unit="%",
        observed_at=now - timedelta(minutes=11),
    ))

    events = HealthMonitor(store).evaluate_resident("elderly-001", now=now)

    assert events == []
