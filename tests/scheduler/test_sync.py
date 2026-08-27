from datetime import date, datetime, timedelta

import pytest

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


def test_force_full_backfill_refetches_from_backfill_start_ignoring_checkpoint(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 11)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 6), date(2026, 1, 10))
    provider.fetch_calls.clear()

    results = sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 10), force_full_backfill=True
    )

    assert results == {"steps": "complete"}
    assert provider.fetch_calls == [("steps", date(2026, 1, 1), date(2026, 1, 10))]
    assert repository.get_readings(conn, "steps", date(2026, 1, 1), date(2026, 1, 10)) == readings
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 10)


def test_normal_sync_after_forced_backfill_resumes_from_new_checkpoint(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 11)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 10), force_full_backfill=True)
    provider.fetch_calls.clear()

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 10), force_full_backfill=False)

    assert provider.fetch_calls == []


def test_chunk_days_less_than_one_raises_value_error(tmp_path):
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(readings_by_metric={"steps": []})

    with pytest.raises(ValueError, match="chunk_days must be >= 1"):
        sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5), chunk_days=0)


def test_checkpoint_capped_below_today_when_chunk_reaches_today(tmp_path):
    """Regression: a caller syncing through the real "today" (e.g. every
    production caller, which always passes end=date.today()) must not have
    its checkpoint advance all the way to today -- today can still gain new
    data (a Garmin activity logged an hour after an earlier sync) that a
    checkpoint sitting at today would cause the next sync to skip entirely,
    since cursor = today + 1 > end = today reports "up_to_date" without
    ever re-fetching."""
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5), today=date(2026, 1, 5))

    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 4)


def test_second_sync_same_day_still_refetches_today_after_capped_checkpoint(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})
    today = date(2026, 1, 5)

    sync_all_metrics(conn, provider, date(2026, 1, 1), today, today=today)
    provider.fetch_calls.clear()
    # A new reading for "today" arrives between the two sync passes --
    # simulates a Garmin activity logged after the first Sync Now click.
    provider._readings_by_metric["steps"] = readings + [_reading(5, value=2.0)]

    results = sync_all_metrics(conn, provider, date(2026, 1, 1), today, today=today)

    assert results == {"steps": "complete"}
    assert provider.fetch_calls == [("steps", date(2026, 1, 5), date(2026, 1, 5))]
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 4)


def test_today_param_has_no_effect_when_chunk_ends_before_today(tmp_path):
    """A deliberately bounded historical backfill (end well before the real
    "today") is unaffected by the today param -- every day in such a range
    is already final, so the checkpoint should still reach end exactly,
    matching test_backfill_persists_all_readings_and_sets_checkpoint's
    established contract."""
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5), today=date(2026, 6, 1))

    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 5)


def test_checkpoint_capped_by_resync_grace_days_when_chunk_reaches_grace_window(tmp_path):
    """resync_grace_days generalizes the today-1 cap above: with
    resync_grace_days=3, the trailing 3 days (today, today-1, today-2)
    should stay unconfirmed -- checkpoint caps at today-3 instead of
    today-1 -- so a routine sync keeps re-walking them until they age out
    of the window."""
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 5),
        today=date(2026, 1, 5), resync_grace_days=3,
    )

    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 2)


def test_resync_grace_days_defaults_to_one_matching_prior_today_capping_behavior(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5), today=date(2026, 1, 5))

    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 4)


def test_grace_window_recovers_a_reading_the_provider_returns_late(tmp_path):
    """Regression for the checkpoint-skip bug: without a grace window, once
    the checkpoint advances past a day, that day is gone for good even if
    the provider's data for it becomes available on a later sync (e.g.
    Garmin computes resting_hr from overnight data with a lag, so the value
    isn't there yet the first time the sync walks past that day). With
    resync_grace_days, the trailing days are re-walked on every sync until
    they age out, so a value that shows up late still gets picked up."""
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(readings_by_metric={"steps": [_reading(d) for d in range(1, 5)]})  # day 5 missing

    sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 5),
        today=date(2026, 1, 5), resync_grace_days=3,
    )
    assert repository.get_readings(conn, "steps", date(2026, 1, 5), date(2026, 1, 5)) == []
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 2)

    # Day 5's reading "arrives" late, simulating Garmin finishing the
    # computation between the two sync passes.
    provider._readings_by_metric["steps"].append(_reading(5))
    provider.fetch_calls.clear()

    results = sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 6),
        today=date(2026, 1, 6), resync_grace_days=3,
    )

    assert results == {"steps": "complete"}
    assert provider.fetch_calls == [("steps", date(2026, 1, 3), date(2026, 1, 6))]
    assert repository.get_readings(conn, "steps", date(2026, 1, 5), date(2026, 1, 5)) == [_reading(5)]
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 3)


def test_grace_window_stops_rewalking_a_day_once_it_ages_out(tmp_path):
    """Once a day falls outside the grace window, it's treated as final
    again -- the sync shouldn't keep re-fetching it forever."""
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(readings_by_metric={"steps": [_reading(d) for d in range(1, 6)]})

    sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 5),
        today=date(2026, 1, 5), resync_grace_days=3,
    )
    # Advance far enough that day 5 (checkpoint+1 .. today-3 boundary) is
    # now outside the grace window.
    provider.fetch_calls.clear()

    sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 10),
        today=date(2026, 1, 10), resync_grace_days=3,
    )

    assert provider.fetch_calls == [("steps", date(2026, 1, 3), date(2026, 1, 10))]
    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 7)


def test_omitting_today_preserves_unconditional_checkpoint_through_end(tmp_path):
    conn = connect(tmp_path / "test.db")
    readings = [_reading(d) for d in range(1, 6)]
    provider = FakeProvider(readings_by_metric={"steps": readings})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5))

    assert repository.get_checkpoint(conn, "fake", "steps") == date(2026, 1, 5)


def test_on_metric_progress_called_once_per_metric_type_with_running_count(tmp_path):
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(readings_by_metric={"steps": [_reading(1)], "resting_hr": [_reading(1)]})
    progress: list[tuple[int, int]] = []

    sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 1),
        on_metric_progress=lambda c, t: progress.append((c, t)),
    )

    assert progress == [(1, 2), (2, 2)]


def test_on_metric_progress_counts_up_to_date_and_failed_metrics_too(tmp_path):
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(
        readings_by_metric={"steps": [_reading(1)], "resting_hr": []},
        fail_metric_types={"steps"},
    )
    progress: list[tuple[int, int]] = []

    sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 1),
        on_metric_progress=lambda c, t: progress.append((c, t)),
    )

    assert progress == [(1, 2), (2, 2)]


def test_on_metric_progress_is_optional(tmp_path):
    conn = connect(tmp_path / "test.db")
    provider = FakeProvider(readings_by_metric={"steps": [_reading(1)]})

    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 1))  # must not raise with no callback given


def test_sync_all_metrics_persists_activities_when_provider_supports_it(tmp_path):
    from core.storage.models import Activity
    conn = connect(tmp_path / "test.db")
    act = Activity(
        id="fake:1",
        source="fake",
        activity_id="1",
        activity_name="Morning 10K",
        activity_type="running",
        sport_type="running",
        start_time=datetime(2026, 1, 2, 8, 0),
        duration_seconds=3000.0,
        distance_meters=10000.0,
        calories=700.0,
        avg_hr=155.0,
        max_hr=172.0,
        avg_speed=3.33,
        max_speed=4.0,
        elevation_gain=50.0,
        elevation_loss=50.0,
        created_at=datetime(2026, 1, 2, 9, 0),
    )

    class _ActivityProvider(FakeProvider):
        def __init__(self):
            super().__init__(readings_by_metric={"activity_duration": [_reading(2, 50.0)]})

        def fetch_activities(self, start, end):
            return [act]

    provider = _ActivityProvider()
    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5))

    stored_activities = repository.get_activities(conn)
    assert len(stored_activities) == 1
    assert stored_activities[0].activity_name == "Morning 10K"
    assert stored_activities[0].activity_type == "running"


def test_sync_all_metrics_persists_activities_for_tonal_workout_duration_metric_type(tmp_path):
    """Regression: the activity-persisting gate was hardcoded to the single
    metric_type name "activity_duration" (Garmin's/Strava's shared name),
    so it silently never fired for Tonal's "tonal_workout_duration" --
    TonalProvider.fetch_activities existed but was dead code, and no
    `activity` rows were ever written for Tonal."""
    from core.storage.models import Activity
    conn = connect(tmp_path / "test.db")
    act = Activity(
        id="tonal:1",
        source="tonal",
        activity_id="1",
        activity_name="Push Workout",
        activity_type="strength_training",
        sport_type="strength_training",
        start_time=datetime(2026, 1, 2, 8, 0),
        duration_seconds=1800.0,
        distance_meters=None,
        calories=None,
        avg_hr=None,
        max_hr=None,
        avg_speed=None,
        max_speed=None,
        elevation_gain=None,
        elevation_loss=None,
        created_at=datetime(2026, 1, 2, 9, 0),
    )

    class _TonalActivityProvider(FakeProvider):
        def __init__(self):
            super().__init__(readings_by_metric={"tonal_workout_duration": [_reading(2, 1800.0)]})
            self.name = "tonal"

        def fetch_activities(self, start, end):
            return [act]

    provider = _TonalActivityProvider()
    sync_all_metrics(conn, provider, date(2026, 1, 1), date(2026, 1, 5))

    stored_activities = repository.get_activities(conn)
    assert len(stored_activities) == 1
    assert stored_activities[0].activity_name == "Push Workout"
    assert stored_activities[0].source == "tonal"


def test_snapshot_metric_type_is_fetched_once_regardless_of_backfill_window(tmp_path):
    """A metric_type a provider declares as snapshot-only (e.g. Tonal's
    per-muscle readiness -- always "right now", no historical range support)
    should be fetched exactly once per sync_all_metrics call, not once per
    chunk. Without this, a 10-year first backfill walks ~122 chunks issuing
    ~122 identical "give me readiness now" calls and writes ~122
    near-duplicate rows."""
    conn = connect(tmp_path / "test.db")

    class _SnapshotProvider(FakeProvider):
        def __init__(self):
            super().__init__(readings_by_metric={
                "readiness_chest": [_reading(1, 70.0)],  # date(2026,1,1) fixture reading
            })

        def snapshot_metric_types(self):
            return frozenset({"readiness_chest"})

        def fetch(self, metric_type, start, end):
            self.fetch_calls.append((metric_type, start, end))
            # A real snapshot endpoint ignores the requested range and
            # always returns "now" -- return the same single reading no
            # matter what (start, end) this call was made with.
            return self._readings_by_metric["readiness_chest"]

    provider = _SnapshotProvider()

    results = sync_all_metrics(conn, provider, date(2016, 1, 1), date(2026, 1, 5), chunk_days=30)

    assert results == {"readiness_chest": "complete"}
    assert len(provider.fetch_calls) == 1, (
        f"expected exactly 1 fetch call for a snapshot metric over a 10-year "
        f"window, got {len(provider.fetch_calls)}"
    )
    assert repository.get_checkpoint(conn, "fake", "readiness_chest") == date(2026, 1, 5)


def test_snapshot_metric_type_still_paced_and_reported_alongside_regular_metrics(tmp_path):
    """A snapshot metric_type and a regular time-series metric_type in the
    same provider don't interfere with each other's chunking/pacing, and
    on_metric_progress still fires once per metric_type either way."""
    conn = connect(tmp_path / "test.db")

    class _MixedProvider(FakeProvider):
        def __init__(self):
            super().__init__(readings_by_metric={
                "readiness_chest": [_reading(1, 70.0)],
                "steps": [_reading(d) for d in range(1, 6)],
            })

        def snapshot_metric_types(self):
            return frozenset({"readiness_chest"})

        def fetch(self, metric_type, start, end):
            self.fetch_calls.append((metric_type, start, end))
            if metric_type == "readiness_chest":
                return self._readings_by_metric["readiness_chest"]
            return [r for r in self._readings_by_metric[metric_type] if start <= r.timestamp.date() <= end]

    provider = _MixedProvider()
    progress_calls = []

    results = sync_all_metrics(
        conn, provider, date(2026, 1, 1), date(2026, 1, 5), chunk_days=2,
        on_metric_progress=lambda completed, total: progress_calls.append((completed, total)),
    )

    assert results == {"readiness_chest": "complete", "steps": "complete"}
    readiness_calls = [c for c in provider.fetch_calls if c[0] == "readiness_chest"]
    steps_calls = [c for c in provider.fetch_calls if c[0] == "steps"]
    assert len(readiness_calls) == 1
    assert len(steps_calls) > 1  # a 5-day range chunked at 2 days -> multiple chunks
    assert progress_calls == [(1, 2), (2, 2)]

