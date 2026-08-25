from datetime import date, datetime

import pytest

from core.providers.tonal import TONAL_METRIC_TYPES, TonalProvider
from core.storage import repository
from core.storage.db import connect
from core.storage.models import Activity


class FakeTonalClient:
    """Stand-in for TonalClient's interface -- TonalClient itself is already
    covered by tests/providers/test_tonal_client.py, so TonalProvider tests
    stub its interface directly rather than mocking HTTP."""

    def __init__(
        self,
        muscle_readiness=None,
        strength_score_history=None,
        activities=None,
        workout_detail=None,
        movements_search_result=None,
        estimate_result=None,
        create_result=None,
    ):
        self.muscle_readiness = muscle_readiness or {}
        self.strength_score_history = strength_score_history or []
        self.activities = activities or []
        self.workout_detail = workout_detail or {}
        self.movements_search_result = movements_search_result or []
        self.estimate_result = estimate_result or {"estimated_duration_min": 30, "set_count": 12}
        self.create_result = create_result or {"workout_id": "w-1", "title": "t", "set_count": 12, "exercise_count": 4}
        self.deleted_workout_ids = []
        self.get_workout_detail_calls = []
        self.search_movements_calls = []
        self.estimate_workout_calls = []
        self.create_workout_calls = []
        self.get_activities_calls: list[int] = []

    def get_muscle_readiness(self):
        return self.muscle_readiness

    def get_strength_score_history(self, limit=20):
        return self.strength_score_history

    def get_activities(self, limit=10):
        self.get_activities_calls.append(limit)
        return self.activities[:limit]

    def get_workout_detail(self, activity_id):
        self.get_workout_detail_calls.append(activity_id)
        return self.workout_detail

    def search_movements(self, query=None, muscle_group=None):
        self.search_movements_calls.append((query, muscle_group))
        return self.movements_search_result

    def estimate_workout(self, blocks):
        self.estimate_workout_calls.append(blocks)
        return self.estimate_result

    def create_workout(self, title, blocks):
        self.create_workout_calls.append((title, blocks))
        return self.create_result

    def delete_workout(self, workout_id):
        self.deleted_workout_ids.append(workout_id)
        return True


def _provider(fake_client):
    return TonalProvider(credential_store=None, tonal_client_factory=lambda credential_store: fake_client)


def test_supported_metric_types_returns_all_14_tonal_metric_types():
    provider = _provider(FakeTonalClient())

    types = provider.supported_metric_types()

    assert types == TONAL_METRIC_TYPES
    assert len(types) == 14
    assert "tonal_readiness_chest" in types
    assert "tonal_strength_score" in types
    assert "tonal_workout_volume" in types
    assert "tonal_workout_duration" in types


def test_snapshot_metric_types_is_exactly_the_11_readiness_metrics():
    """readiness has no historical range support -- it always returns "right
    now" regardless of the requested date range (see _fetch_readiness).
    core.scheduler.sync.sync_all_metrics duck-types this method (hasattr) to
    fetch these once per sync pass instead of once per chunk; strength score
    and the two workout-derived metrics are real time-series data and must
    NOT be in this set."""
    provider = _provider(FakeTonalClient())

    snapshot_types = provider.snapshot_metric_types()

    assert snapshot_types == {t for t in TONAL_METRIC_TYPES if t.startswith("tonal_readiness_")}
    assert len(snapshot_types) == 11
    assert "tonal_strength_score" not in snapshot_types
    assert "tonal_workout_volume" not in snapshot_types
    assert "tonal_workout_duration" not in snapshot_types


def test_fetch_readiness_returns_exactly_one_reading_regardless_of_date_range():
    fake_client = FakeTonalClient(muscle_readiness={"Chest": 82.5})
    provider = _provider(fake_client)

    narrow_range = provider.fetch("tonal_readiness_chest", date(2020, 1, 1), date(2020, 1, 1))
    wide_range = provider.fetch("tonal_readiness_chest", date(2000, 1, 1), date(2030, 1, 1))

    assert len(narrow_range) == 1
    assert len(wide_range) == 1
    assert narrow_range[0].value == 82.5
    assert narrow_range[0].metric_type == "tonal_readiness_chest"
    assert narrow_range[0].source == "tonal"
    assert narrow_range[0].unit == "percent"
    # naive UTC "now" timestamp, not derived from the requested range
    assert narrow_range[0].timestamp.tzinfo is None


def test_fetch_readiness_returns_empty_when_muscle_absent_from_response():
    fake_client = FakeTonalClient(muscle_readiness={})
    provider = _provider(fake_client)

    result = provider.fetch("tonal_readiness_glutes", date(2026, 1, 1), date(2026, 1, 31))

    assert result == []


def test_fetch_strength_score_filters_entries_by_date_range():
    fake_client = FakeTonalClient(
        strength_score_history=[
            {"date": "2026-01-05", "overall": 55.0, "upper": 50.0, "lower": 60.0, "core": 55.0},
            {"date": "2026-01-15", "overall": 58.0, "upper": 52.0, "lower": 62.0, "core": 57.0},
            {"date": "2026-02-01", "overall": 60.0, "upper": 55.0, "lower": 63.0, "core": 59.0},
        ]
    )
    provider = _provider(fake_client)

    result = provider.fetch("tonal_strength_score", date(2026, 1, 1), date(2026, 1, 31))

    assert len(result) == 2
    assert {r.value for r in result} == {55.0, 58.0}
    assert all(r.metric_type == "tonal_strength_score" for r in result)
    assert all(r.source == "tonal" for r in result)


def test_fetch_raises_value_error_for_unsupported_metric_type():
    provider = _provider(FakeTonalClient())

    with pytest.raises(ValueError):
        provider.fetch("not_a_real_metric", date(2026, 1, 1), date(2026, 1, 31))


def test_fetch_workout_volume_and_duration_filter_by_date_and_map_fields():
    fake_client = FakeTonalClient(
        activities=[
            {
                "activity_id": "act-1",
                "date": "2026-01-10T08:00:00Z",
                "title": "Push Workout",
                "type": "Push",
                "duration_seconds": 1800.0,
                "total_volume_lbs": 5400.0,
            },
            {
                "activity_id": "act-2",
                "date": "2026-03-01T08:00:00Z",
                "title": "Pull Workout",
                "type": "Pull",
                "duration_seconds": 2000.0,
                "total_volume_lbs": 6200.0,
            },
        ]
    )
    provider = _provider(fake_client)

    volume_readings = provider.fetch("tonal_workout_volume", date(2026, 1, 1), date(2026, 1, 31))
    duration_readings = provider.fetch("tonal_workout_duration", date(2026, 1, 1), date(2026, 1, 31))

    assert len(volume_readings) == 1
    assert volume_readings[0].value == 5400.0
    assert volume_readings[0].unit == "lbs"
    assert len(duration_readings) == 1
    assert duration_readings[0].value == 1800.0
    assert duration_readings[0].unit == "seconds"


def test_fetch_activities_produces_activity_rows_with_source_tonal():
    fake_client = FakeTonalClient(
        activities=[
            {
                "activity_id": "act-1",
                "date": "2026-01-10T08:00:00Z",
                "title": "Push Workout",
                "type": "Push",
                "duration_seconds": 1800.0,
                "total_volume_lbs": 5400.0,
            }
        ]
    )
    provider = _provider(fake_client)

    activities = provider.fetch_activities(date(2026, 1, 1), date(2026, 1, 31))

    assert len(activities) == 1
    activity = activities[0]
    assert isinstance(activity, Activity)
    assert activity.source == "tonal"
    assert activity.id == "tonal:act-1"
    assert activity.activity_id == "act-1"
    assert activity.activity_name == "Push Workout"
    assert activity.activity_type == "strength_training"
    assert activity.sport_type == "Push"
    assert activity.duration_seconds == 1800.0
    assert activity.distance_meters is None
    assert activity.start_time == datetime(2026, 1, 10, 8, 0)


def test_fetch_caches_raw_activities_across_repeated_calls_within_one_instance():
    """Regression: fetch() used to call self._client.get_activities() fresh
    every single time, so sync_all_metrics's per-(metric_type, date-chunk)
    calls to fetch("tonal_workout_volume", ...) and
    fetch("tonal_workout_duration", ...) -- plus fetch_activities() itself --
    each re-downloaded the entire (server-side-unfiltered) workout history.
    They should now share one cached response per instance."""
    fake_client = FakeTonalClient(
        activities=[
            {
                "activity_id": "act-1",
                "date": "2026-01-10T08:00:00Z",
                "title": "Push Workout",
                "type": "Push",
                "duration_seconds": 1800.0,
                "total_volume_lbs": 5400.0,
            }
        ]
    )
    provider = _provider(fake_client)

    provider.fetch("tonal_workout_volume", date(2026, 1, 1), date(2026, 1, 31))
    provider.fetch("tonal_workout_duration", date(2026, 1, 1), date(2026, 1, 31))
    provider.fetch_activities(date(2026, 1, 1), date(2026, 1, 31))

    assert len(fake_client.get_activities_calls) == 1


def test_fetch_activities_excludes_out_of_range_dates():
    fake_client = FakeTonalClient(
        activities=[
            {
                "activity_id": "act-1",
                "date": "2026-01-10T08:00:00Z",
                "title": "Push Workout",
                "type": "Push",
                "duration_seconds": 1800.0,
                "total_volume_lbs": 5400.0,
            },
            {
                "activity_id": "act-2",
                "date": "2026-06-01T08:00:00Z",
                "title": "Pull Workout",
                "type": "Pull",
                "duration_seconds": 2000.0,
                "total_volume_lbs": 6200.0,
            },
        ]
    )
    provider = _provider(fake_client)

    activities = provider.fetch_activities(date(2026, 1, 1), date(2026, 1, 31))

    assert len(activities) == 1
    assert activities[0].activity_id == "act-1"


def test_get_workout_detail_persists_strength_sets_and_returns_detail_dict(tmp_path):
    conn = connect(tmp_path / "test.db")
    fake_client = FakeTonalClient(
        workout_detail={
            "total_duration_seconds": 1800,
            "total_volume_lbs": 5400.0,
            "sets": [
                {
                    "movement_id": "mv-bench",
                    "is_warm_up": True,
                    "reps": 10,
                    "weight_lbs": 45.0,
                    "volume_lbs": 450.0,
                    "one_rep_max": 100.0,
                    "max_power_watts": 200.0,
                    "rom_inches": 18.0,
                    "struggling_score": 0.1,
                    "side": "Both",
                },
                {
                    "movement_id": "mv-bench",
                    "is_warm_up": False,
                    "reps": 8,
                    "weight_lbs": 135.0,
                    "volume_lbs": 1080.0,
                    "one_rep_max": 180.0,
                    "max_power_watts": 450.0,
                    "rom_inches": 18.5,
                    "struggling_score": 0.4,
                    "side": "Both",
                },
            ],
        }
    )
    provider = _provider(fake_client)

    detail = provider.get_workout_detail(conn, "act-1")

    assert detail == fake_client.workout_detail
    assert fake_client.get_workout_detail_calls == ["act-1"]

    persisted = repository.get_strength_sets(conn, "tonal:act-1")
    assert len(persisted) == 2
    assert persisted[0].movement_id == "mv-bench"
    assert persisted[0].is_warm_up is True
    assert persisted[0].reps == 10
    assert persisted[1].is_warm_up is False
    assert persisted[1].reps == 8
    assert persisted[0].id == "tonal:act-1:0"
    assert persisted[1].id == "tonal:act-1:1"


def test_search_movements_estimate_create_delete_are_passthroughs_to_client():
    fake_client = FakeTonalClient(
        movements_search_result=[{"id": "mv-1", "name": "Bench Press"}],
    )
    provider = _provider(fake_client)

    result = provider.search_movements(query="bench", muscle_group="Chest")
    assert result == [{"id": "mv-1", "name": "Bench Press"}]
    assert fake_client.search_movements_calls == [("bench", "Chest")]

    blocks = [{"exercises": [{"movement_id": "mv-1", "sets": 3, "reps": 10}]}]
    estimate = provider.estimate_workout(blocks)
    assert estimate == fake_client.estimate_result
    assert fake_client.estimate_workout_calls == [blocks]

    created = provider.create_workout("Push Day", blocks)
    assert created == fake_client.create_result
    assert fake_client.create_workout_calls == [("Push Day", blocks)]

    deleted = provider.delete_workout("w-1")
    assert deleted is True
    assert fake_client.deleted_workout_ids == ["w-1"]
