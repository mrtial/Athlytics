from datetime import date, datetime, timedelta

from core.providers.fake import FakeProvider
from core.scheduler.sync import sync_all_metrics
from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading


def _reading(day_number: int, value: float = 1.0) -> MetricReading:
    timestamp = datetime(2026, 1, 1) + timedelta(days=day_number - 1)
    return MetricReading("fake", "steps", timestamp, value, "count")


def test_backfill_persists_all_readings_and_sets_checkpoint(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    results = sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5))

    assert results == {"steps": "complete"}
    assert repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 5)) == readings
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 5)


def test_resync_resumes_from_checkpoint_without_refetching_synced_days(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 11)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5))
    provider.fetch_calls.clear()
    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 10))

    assert provider.fetch_calls == [("steps", date(2026, 1, 6), date(2026, 1, 10))]
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 10)


def test_second_sync_with_no_new_days_reports_up_to_date(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5))
    results = sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5))

    assert results == {"steps": "up_to_date"}


def test_failure_in_one_metric_type_does_not_block_others(tmp_path):
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(
        readings_by_metric={"steps": [_reading(1)], "resting_hr": []},
        fail_metric_types={"steps"},
    )

    results = sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 1))

    assert results == {"steps": "failed", "resting_hr": "complete"}
    assert repository.get_checkpoint(conn, "fake", "steps") is None
    assert repository.get_checkpoint(conn, "fake", "resting_hr") == date(2026, 1, 1)


def test_rate_limit_triggers_backoff_and_resume(tmp_path):
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(
        readings_by_metric={"steps": [_reading(1)]},
        rate_limit_metric_types={"steps": 2},
    )
    sleep_calls = []

    results = sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 1), sleep_fn=sleep_calls.append
    )

    assert results == {"steps": "complete"}
    assert sleep_calls == [1.0, 2.0]
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 1)


def test_exhausting_retries_on_persistent_rate_limit_marks_failed(tmp_path):
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(
        readings_by_metric={"steps": [_reading(1)]},
        rate_limit_metric_types={"steps": 10},
    )

    results = sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 1), max_retries=2, sleep_fn=lambda _: None
    )

    assert results == {"steps": "failed"}
    assert repository.get_checkpoint(conn, "fake", "steps") is None


def test_backfill_paces_between_chunks_but_not_after_the_last_one(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 61)]
    provider = FakeProvider(readings_by_metric={"steps": readings})
    sleep_calls = []

    sync_all_metrics(
        conn,
        provider,
        date(2026, 1, 1),
        date(2026, 3, 1),
        chunk_days=30,
        pace_seconds=0.5,
        sleep_fn=sleep_calls.append,
    )

    assert sleep_calls == [0.5]
