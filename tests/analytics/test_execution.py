import pytest

from core.analytics.execution import format_pace_per_mile


def test_format_pace_per_mile_converts_meters_per_second_to_min_sec_per_mile():
    assert format_pace_per_mile(2.675) == "10:02"
    assert format_pace_per_mile(2.92) == "9:11"
    assert format_pace_per_mile(4.4704) == "6:00"  # exactly 10 mph


@pytest.mark.parametrize("speed", [None, 0, 0.0, -1.0])
def test_format_pace_per_mile_is_none_without_a_positive_speed(speed):
    assert format_pace_per_mile(speed) is None


from core.analytics.execution import downsample_series


def _sample(t, hr=None, speed=None, cadence=None, power=None, elevation=None, distance=None):
    return {"elapsed_s": t, "heart_rate": hr, "speed_mps": speed, "cadence_spm": cadence,
            "power_watts": power, "elevation_m": elevation, "distance_m": distance}


def test_downsample_series_averages_each_bucket_and_keeps_the_last_distance():
    samples = [_sample(t, hr=100 + t, speed=3.0, distance=t * 3.0) for t in range(10)]

    points = downsample_series(samples, resolution_seconds=5)

    assert [p["t_seconds"] for p in points] == [0, 5]
    assert points[0]["heart_rate"] == pytest.approx(102.0)   # mean of 100..104
    assert points[1]["heart_rate"] == pytest.approx(107.0)   # mean of 105..109
    assert points[1]["distance_miles"] == pytest.approx(27.0 / 1609.344, abs=1e-3)  # last sample in the bucket
    assert points[0]["pace_per_mile"] == "8:56"


def test_downsample_series_ignores_missing_values_and_keeps_none_when_a_bucket_has_none():
    samples = [_sample(0, hr=140), _sample(1, hr=None), _sample(2, hr=150, power=None)]

    point = downsample_series(samples, resolution_seconds=10)[0]

    assert point["heart_rate"] == pytest.approx(145.0)
    assert point["power_watts"] is None and point["cadence_spm"] is None and point["pace_per_mile"] is None


def test_downsample_series_handles_empty_input_and_unordered_samples():
    assert downsample_series([], resolution_seconds=30) == []

    points = downsample_series([_sample(40, hr=150), _sample(0, hr=100)], resolution_seconds=30)

    assert [p["t_seconds"] for p in points] == [0, 30]


# ---- build_training_execution -------------------------------------------------

import json
from datetime import date, datetime
from pathlib import Path

from core.analytics.execution import build_training_execution
from core.providers.garmin import GarminProvider
from core.storage import repository
from core.storage.db import connect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "garmin"


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _seed_real_runs(conn):
    """The captured outdoor run (Jan 10, local 09:17) and treadmill run (Jan 11, local 10:44), with laps."""
    repository.upsert_activities(conn, GarminProvider._parse_activity_records(_fixture("get_activities_by_date_detail")))
    repository.replace_activity_laps(conn, "garmin:9000000001", GarminProvider._parse_activity_laps(_fixture("get_activity_splits_run"), "garmin:9000000001"))
    repository.replace_activity_laps(conn, "garmin:9000000002", GarminProvider._parse_activity_laps(_fixture("get_activity_splits_treadmill"), "garmin:9000000002"))


def test_execution_summarizes_an_outdoor_run_with_zone_share_effect_and_load(tmp_path):
    conn = connect(tmp_path / "t.db")
    _seed_real_runs(conn)

    result = build_training_execution(conn, date(2026, 1, 10), date(2026, 1, 10))

    assert [r["activity_id"] for r in result["runs"]] == ["garmin:9000000001"]
    run = result["runs"][0]
    assert run["date"] == "2026-01-10" and run["weekday"] == "Sat" and run["is_treadmill"] is False
    assert run["distance_miles"] == pytest.approx(7.34, abs=0.01) and run["avg_pace_per_mile"] == "9:35"
    assert run["pct_zone_1_2"] == pytest.approx(6.4, abs=0.1)
    assert run["pct_zone_3"] == pytest.approx(78.9, abs=0.1)
    assert run["pct_zone_4_5"] == pytest.approx(14.7, abs=0.1)
    assert run["aerobic_training_effect"] == pytest.approx(3.8, abs=0.01) and run["training_effect_label"] == "AEROBIC_BASE"
    assert run["training_load"] == pytest.approx(154.8, abs=0.1)
    assert run["laps_available"] is True


def test_execution_heart_rate_drift_compares_the_first_and_last_halves_of_full_laps(tmp_path):
    conn = connect(tmp_path / "t.db")
    _seed_real_runs(conn)
    laps = [l for l in _fixture("get_activity_splits_run")["lapDTOs"] if l["distance"] >= 804.0]  # drops the 0.34 mi finish
    assert len(laps) == 7  # first 3, skipped middle, last 3

    def weighted_hr(chunk):
        return sum(l["averageHR"] * l["duration"] for l in chunk) / sum(l["duration"] for l in chunk)

    expected = weighted_hr(laps[-3:]) - weighted_hr(laps[:3])

    drift = build_training_execution(conn, date(2026, 1, 10), date(2026, 1, 10))["runs"][0]["drift"]

    assert drift["hr_drift_bpm"] == pytest.approx(expected, abs=0.1)
    assert drift["hr_drift_bpm"] > 0
    assert drift["pace_change_pct"] is not None and drift["laps_used"] == 6


def test_execution_flags_treadmill_runs_and_skips_drift_when_too_few_laps(tmp_path):
    conn = connect(tmp_path / "t.db")
    _seed_real_runs(conn)

    result = build_training_execution(conn, date(2026, 1, 11), date(2026, 1, 11))

    run = result["runs"][0]
    assert run["is_treadmill"] is True and run["sport_type"] == "treadmill_running"
    assert run["drift"] is None
    assert any("treadmill" in note.lower() for note in result["notes"])


def test_execution_weekly_rollup_totals_and_counts_runs_mostly_above_zone_2(tmp_path):
    conn = connect(tmp_path / "t.db")
    _seed_real_runs(conn)

    weekly = build_training_execution(conn, date(2026, 1, 10), date(2026, 1, 11))["weekly"]

    assert weekly["run_count"] == 2
    assert weekly["total_distance_miles"] == pytest.approx(7.34 + 1.71, abs=0.02)
    assert weekly["total_training_load"] == pytest.approx(154.8 + 50.4, abs=0.2)
    assert weekly["runs_with_zone_data"] == 2 and weekly["runs_mostly_above_zone_2"] == 2
    z = [4219.45 + 1070.5, 64.034 + 205.625 + 30.997 + 315.001]
    assert weekly["pct_zone_1_2"] == pytest.approx(z[1] / z[0] * 100, abs=0.2)


def test_execution_leaves_zone_data_none_for_activities_without_it(tmp_path):
    conn = connect(tmp_path / "t.db")
    repository.upsert_activities(conn, GarminProvider._parse_activity_records(_fixture("get_activities_by_date")))

    result = build_training_execution(conn, date(2000, 1, 1), date(2100, 1, 1))

    run = result["runs"][0]
    assert run["pct_zone_1_2"] is None and run["zone_seconds"] is None and run["training_load"] is None
    assert result["weekly"]["runs_with_zone_data"] == 0 and result["weekly"]["pct_zone_1_2"] is None
    assert any("zone data" in note.lower() for note in result["notes"])


def test_execution_assigns_runs_to_their_local_date_not_the_utc_date(tmp_path):
    conn = connect(tmp_path / "t.db")
    late = GarminProvider._parse_activity_records([{
        "activityId": 5, "activityName": "Late run", "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-01-11 02:00:00", "startTimeLocal": "2026-01-10 22:00:00",
        "duration": 1800.0, "distance": 5000.0,
    }])
    repository.upsert_activities(conn, late)

    on_the_10th = build_training_execution(conn, date(2026, 1, 10), date(2026, 1, 10))
    on_the_11th = build_training_execution(conn, date(2026, 1, 11), date(2026, 1, 11))

    assert [r["date"] for r in on_the_10th["runs"]] == ["2026-01-10"]
    assert on_the_11th["runs"] == []


def test_execution_defaults_to_running_and_handles_an_empty_range(tmp_path):
    conn = connect(tmp_path / "t.db")
    repository.upsert_activities(conn, GarminProvider._parse_activity_records([{
        "activityId": 6, "activityName": "Ride", "activityType": {"typeKey": "cycling"},
        "startTimeGMT": "2026-01-10 12:00:00", "startTimeLocal": "2026-01-10 07:00:00", "duration": 3600.0, "distance": 20000.0,
    }]))

    default = build_training_execution(conn, date(2026, 1, 10), date(2026, 1, 10))
    rides = build_training_execution(conn, date(2026, 1, 10), date(2026, 1, 10), activity_type="cycling")

    assert default["runs"] == [] and default["weekly"]["run_count"] == 0 and default["weekly"]["total_distance_miles"] == 0
    assert default["weekly"]["pct_zone_1_2"] is None
    assert len(rides["runs"]) == 1
