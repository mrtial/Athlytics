import dataclasses
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.storage import repository
from core.storage.db import connect
from core.storage.models import Activity, CoachNote, MetricReading, Target, TrainingPlan
from mcp_server.server import DB_PATH_ENV_VAR, _db_path, _with_utc_tzinfo, mcp


def test_server_instance_is_an_mcp_server():
    assert isinstance(mcp, MCPServer)
    assert mcp.name == "Athlytics"


def test_db_path_respects_environment_override(monkeypatch, tmp_path):
    custom_db = tmp_path / "custom.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(custom_db))
    assert _db_path() == custom_db


# RFC 3339 `date-time` requires an explicit UTC offset -- e.g. trailing "Z"
# or "+00:00" -- unlike naive datetime.isoformat() output. See
# mcp_server.server._with_utc_tzinfo.
_RFC3339_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def test_with_utc_tzinfo_attaches_offset_without_mutating_source():
    from datetime import timezone

    reading = MetricReading("garmin", "steps", datetime(2026, 1, 2), 8000.0, "count")
    wired = _with_utc_tzinfo(reading)

    assert wired.timestamp == datetime(2026, 1, 2, tzinfo=timezone.utc)
    # The naive-only invariant enforced by MetricReading.__post_init__ must
    # still hold for the original, internally-used instance.
    assert reading.timestamp.tzinfo is None


def test_with_utc_tzinfo_bypasses_activity_naive_only_post_init():
    """Activity.__post_init__ rejects tz-aware start_time/created_at when
    constructed normally; _with_utc_tzinfo must attach tzinfo via a copy
    that doesn't re-run that validation, not via dataclasses.replace."""
    now = datetime(2026, 1, 1, 12, 0)
    activity = Activity(
        "garmin:101", "garmin", "101", "Morning 5K", "running", "running",
        now, 1800.0, 5000.0, 350.0, 150.0, 165.0, 2.78, 3.2, 30.0, 30.0, now,
    )

    wired = _with_utc_tzinfo(activity)

    from datetime import timezone

    assert wired.start_time.tzinfo is timezone.utc
    assert wired.created_at.tzinfo is timezone.utc
    assert activity.start_time.tzinfo is None
    assert activity.created_at.tzinfo is None


def test_with_utc_tzinfo_is_a_noop_for_date_only_fields():
    target = Target(
        "t-1", "hrv", 65.0, "gte", "daily", date(2026, 1, 1), None, "active", None,
        datetime(2026, 1, 1, 12, 0),
    )
    wired = _with_utc_tzinfo(target)
    assert wired.start_date == date(2026, 1, 1)
    assert wired.end_date is None


@pytest.mark.anyio
async def test_list_metrics_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    repository.upsert_readings(
        conn, [MetricReading("garmin", "resting_hr", datetime(2026, 1, 1), 52.0, "bpm")]
    )
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("list_metrics", {})

    assert result.is_error is not True
    assert result.structured_content == {
        "result": [
            {
                "metric_type": "resting_hr",
                "earliest_date": "2026-01-01",
                "latest_date": "2026-01-01",
                "reading_count": 1,
                "unit": "bpm",
            }
        ]
    }


@pytest.mark.anyio
async def test_get_metric_series_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    repository.upsert_readings(
        conn,
        [
            MetricReading("garmin", "steps", datetime(2026, 1, 2), 8000.0, "count"),
            MetricReading("garmin", "steps", datetime(2026, 2, 1), 9000.0, "count"),
        ],
    )
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_metric_series", {"metric_type": "steps", "start": "2026-01-01", "end": "2026-01-31"}
        )

    assert result.is_error is not True
    assert len(result.structured_content["result"]) == 1
    assert result.structured_content["result"][0]["value"] == 8000.0
    # Regression: timestamp must carry an explicit UTC offset (RFC 3339
    # `date-time`), not a naive isoformat() string -- see
    # mcp_server.server._with_utc_tzinfo.
    assert result.structured_content["result"][0]["timestamp"] == "2026-01-02T00:00:00Z"


@pytest.mark.anyio
async def test_get_trend_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()
    readings = [
        MetricReading(
            "garmin", "steps", datetime.combine(today - timedelta(days=d), time.min), 1000.0, "count"
        )
        for d in range(7)
    ]
    repository.upsert_readings(conn, readings)
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_trend", {"metric_type": "steps", "window": 7})

    assert result.is_error is not True
    assert result.structured_content["metric_type"] == "steps"
    assert result.structured_content["window_days"] == 7
    assert result.structured_content["current"]["average"] == 1000.0


@pytest.mark.anyio
async def test_get_anomalies_tool_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()
    hr_values = [50.0] * 9 + [75.0]
    readings = [
        MetricReading(
            "garmin", "resting_hr", datetime.combine(today - timedelta(days=9 - d), time.min), v, "bpm"
        )
        for d, v in enumerate(hr_values)
    ]
    repository.upsert_readings(conn, readings)
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_anomalies", {})

    assert result.is_error is not True


@pytest.mark.anyio
async def test_actionable_read_tools_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    now = datetime(2026, 1, 1, 12, 0)
    rep_id = repository.save_report(conn, "Report 1", "Content 1", now)
    repository.save_target(
        conn,
        Target("t-1", "hrv", 65.0, "gte", "daily", date(2026, 1, 1), None, "active", None, now),
    )
    repository.save_training_plan(
        conn,
        TrainingPlan("p-1", "Base", None, date(2026, 1, 1), date(2026, 3, 1), "{}", "active", now),
    )
    repository.save_coach_note(
        conn,
        CoachNote("n-1", date(2026, 1, 1), "feeling", "Feeling strong.", None, now),
    )
    repository.upsert_activities(
        conn,
        [
            Activity(
                "garmin:101",
                "garmin",
                "101",
                "Morning 5K",
                "running",
                "running",
                now,
                1800.0,
                5000.0,
                350.0,
                150.0,
                165.0,
                2.78,
                3.2,
                30.0,
                30.0,
                now,
            )
        ],
    )
    conn.close()

    # Regression: every created_at/start_time below must carry an explicit
    # UTC offset (RFC 3339 `date-time`), not a naive isoformat() string --
    # see mcp_server.server._with_utc_tzinfo.
    expected_timestamp = "2026-01-01T12:00:00Z"

    async with Client(mcp) as client:
        # get_report
        res_rep = await client.call_tool("get_report", {"id": rep_id})
        assert res_rep.structured_content["title"] == "Report 1"
        assert res_rep.structured_content["created_at"] == expected_timestamp

        # get_targets
        res_tar = await client.call_tool("get_targets", {"status": "active"})
        assert len(res_tar.structured_content["result"]) == 1
        assert res_tar.structured_content["result"][0]["metric_type"] == "hrv"
        assert res_tar.structured_content["result"][0]["created_at"] == expected_timestamp

        # get_training_plans
        res_plan = await client.call_tool("get_training_plans", {"status": "active"})
        assert len(res_plan.structured_content["result"]) == 1
        assert res_plan.structured_content["result"][0]["title"] == "Base"
        assert res_plan.structured_content["result"][0]["created_at"] == expected_timestamp

        # get_coach_notes
        res_notes = await client.call_tool("get_coach_notes", {"limit": 5})
        assert len(res_notes.structured_content["result"]) == 1
        assert res_notes.structured_content["result"][0]["category"] == "feeling"
        assert res_notes.structured_content["result"][0]["created_at"] == expected_timestamp

        # get_activities
        res_acts = await client.call_tool("get_activities", {"activity_type": "running"})
        assert len(res_acts.structured_content["result"]) == 1
        assert res_acts.structured_content["result"][0]["activity_name"] == "Morning 5K"
        assert res_acts.structured_content["result"][0]["start_time"] == expected_timestamp
        assert res_acts.structured_content["result"][0]["created_at"] == expected_timestamp



@pytest.mark.anyio
async def test_action_write_tools_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    async with Client(mcp) as client:
        # set_target
        res_tar = await client.call_tool(
            "set_target",
            {
                "metric_type": "steps",
                "target_value": 10000.0,
                "operator": "gte",
                "target_window": "daily",
                "start_date": "2026-01-01",
                "notes": "Target 10k daily",
            },
        )
        assert res_tar.is_error is not True
        target_id = res_tar.structured_content["id"]
        assert res_tar.structured_content["target_value"] == 10000.0
        assert _RFC3339_DATETIME.match(res_tar.structured_content["created_at"])

        # delete_target
        res_del = await client.call_tool("delete_target", {"target_id": target_id})
        assert res_del.is_error is not True
        assert res_del.structured_content == {"result": True}

        # save_training_plan
        res_plan = await client.call_tool(
            "save_training_plan",
            {
                "title": "Marathon Build",
                "goal_description": "Sub-3:30",
                "start_date": "2026-02-01",
                "target_date": "2026-05-31",
                "plan_json": '{"phases": ["Base", "Build"]}',
            },
        )
        assert res_plan.is_error is not True
        plan_id = res_plan.structured_content["id"]
        assert res_plan.structured_content["title"] == "Marathon Build"
        assert _RFC3339_DATETIME.match(res_plan.structured_content["created_at"])

        # update_plan_status
        res_up = await client.call_tool(
            "update_plan_status", {"plan_id": plan_id, "status": "paused"}
        )
        assert res_up.is_error is not True
        assert res_up.structured_content["status"] == "paused"
        assert _RFC3339_DATETIME.match(res_up.structured_content["created_at"])

        # log_coach_note
        res_note = await client.call_tool(
            "log_coach_note",
            {
                "date": "2026-01-10",
                "category": "injury",
                "note": "Knee soreness after intervals.",
                "tags": ["knee", "intervals"],
            },
        )
        assert res_note.is_error is not True
        assert res_note.structured_content["category"] == "injury"
        assert _RFC3339_DATETIME.match(res_note.structured_content["created_at"])


@pytest.mark.anyio
async def test_sync_garmin_data_tool_raises_when_credentials_not_found(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    async with Client(mcp) as client:
        result = await client.call_tool("sync_garmin_data", {"days": 7})

    assert result.is_error is True


@pytest.mark.anyio
async def test_sync_garmin_data_tool_forwards_force_full_history(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore

    secret_key_path = tmp_path / ".env"
    credentials_path = tmp_path / "garmin_credentials.enc"
    secret_key = get_or_create_secret_key(secret_key_path)
    CredentialStore(secret_key, credentials_path).save({"email": "a@example.com", "password": "x"})

    class _StubProvider:
        name = "garmin"

        def __init__(self, *args, **kwargs):
            pass

        def sync_hydration(self, conn, start_date, end_date, force_full_history):
            return "skipped (full history sync)" if force_full_history else "0 nights"

    captured = {}

    def fake_sync_all_metrics(conn, provider, backfill_start, end, **kwargs):
        captured.update(kwargs)
        return {"resting_hr": "complete"}

    monkeypatch.setattr("core.providers.garmin.GarminProvider", _StubProvider)
    monkeypatch.setattr("core.scheduler.sync.sync_all_metrics", fake_sync_all_metrics)

    async with Client(mcp) as client:
        result = await client.call_tool("sync_garmin_data", {"days": 7, "force_full_history": True})

    assert result.is_error is not True
    assert captured.get("force_full_backfill") is True


def _save_stub_garmin_credentials(tmp_path):
    """Write a credentials file so the connected-check passes, mirroring
    test_sync_garmin_data_tool_forwards_force_full_history's setup."""
    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore

    secret_key_path = tmp_path / ".env"
    credentials_path = tmp_path / "garmin_credentials.enc"
    secret_key = get_or_create_secret_key(secret_key_path)
    CredentialStore(secret_key, credentials_path).save({"email": "a@example.com", "password": "x"})


@pytest.mark.anyio
async def test_sync_garmin_data_hydrates_sleep_detail_and_advances_checkpoint(tmp_path, monkeypatch):
    """sync_garmin_data now delegates its checkpoint math entirely to
    GarminProvider.sync_hydration (core/providers/garmin.py) -- this test
    only exercises that sync_garmin_data plumbs that call's return value
    into results["garmin_sleep_detail"] unmodified, mirroring how
    sync_tonal_data delegates to TonalProvider.sync_hydration. The
    grace-days checkpoint math itself is exercised directly against the
    real GarminProvider.sync_hydration in tests/providers/test_garmin.py."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()
    _save_stub_garmin_credentials(tmp_path)

    class _StubProvider:
        name = "garmin"

        def __init__(self, *args, **kwargs):
            pass

        def sync_hydration(self, conn, start_date, end_date, force_full_history):
            assert force_full_history is False
            repository.set_checkpoint(conn, "garmin", "garmin_sleep_detail", end_date)
            return "2 nights (8 stage segments, 5 restless moments)"

    def fake_sync_all_metrics(conn, provider, backfill_start, end, **kwargs):
        return {"resting_hr": "complete"}

    monkeypatch.setattr("core.providers.garmin.GarminProvider", _StubProvider)
    monkeypatch.setattr("core.scheduler.sync.sync_all_metrics", fake_sync_all_metrics)

    async with Client(mcp) as client:
        result = await client.call_tool("sync_garmin_data", {"days": 3})

    assert result.is_error is not True
    assert "2 nights" in result.structured_content["garmin_sleep_detail"]

    conn = connect(db_path)
    checkpoint = repository.get_checkpoint(conn, "garmin", "garmin_sleep_detail")
    conn.close()
    assert checkpoint == date.today()


@pytest.mark.anyio
async def test_sync_garmin_data_skips_sleep_hydration_on_full_history(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()
    _save_stub_garmin_credentials(tmp_path)

    class _StubProvider:
        name = "garmin"

        def __init__(self, *args, **kwargs):
            pass

        def sync_hydration(self, conn, start_date, end_date, force_full_history):
            if force_full_history:
                return "skipped (full history sync)"
            raise AssertionError("must not hydrate on force_full_history=True")

    def fake_sync_all_metrics(conn, provider, backfill_start, end, **kwargs):
        return {"resting_hr": "complete"}

    monkeypatch.setattr("core.providers.garmin.GarminProvider", _StubProvider)
    monkeypatch.setattr("core.scheduler.sync.sync_all_metrics", fake_sync_all_metrics)

    async with Client(mcp) as client:
        result = await client.call_tool("sync_garmin_data", {"days": 3, "force_full_history": True})

    assert result.is_error is not True
    assert result.structured_content["garmin_sleep_detail"] == "skipped (full history sync)"

    conn = connect(db_path)
    checkpoint = repository.get_checkpoint(conn, "garmin", "garmin_sleep_detail")
    conn.close()
    assert checkpoint is None


@pytest.mark.anyio
async def test_refetch_garmin_metric_range_tool_raises_when_credentials_not_found(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    async with Client(mcp) as client:
        result = await client.call_tool(
            "refetch_garmin_metric_range",
            {"metric_type": "resting_hr", "start": "2026-08-17", "end": "2026-08-17"},
        )

    assert result.is_error is True


@pytest.mark.anyio
async def test_refetch_garmin_metric_range_tool_upserts_readings_and_reports_still_missing_dates(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()
    _save_stub_garmin_credentials(tmp_path)

    class _StubProvider:
        name = "garmin"

        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, metric_type, start, end):
            # Simulate Garmin having a value for `start` but not `end` --
            # a real partial-range response.
            return [MetricReading("garmin", metric_type, datetime.combine(start, time.min), 55.0, "bpm")]

    monkeypatch.setattr("core.providers.garmin.GarminProvider", _StubProvider)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "refetch_garmin_metric_range",
            {"metric_type": "resting_hr", "start": "2026-08-24", "end": "2026-08-25"},
        )

    assert result.is_error is not True
    assert result.structured_content["readings_found"] == 1
    assert result.structured_content["still_missing_dates"] == ["2026-08-25"]

    conn = connect(db_path)
    stored = repository.get_readings(conn, "resting_hr", date(2026, 8, 24), date(2026, 8, 25))
    conn.close()
    assert len(stored) == 1
    assert stored[0].timestamp.date() == date(2026, 8, 24)


@pytest.mark.anyio
async def test_refetch_garmin_metric_range_tool_never_touches_the_sync_checkpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    repository.set_checkpoint(conn, "garmin", "resting_hr", date(2026, 8, 26))
    conn.close()
    _save_stub_garmin_credentials(tmp_path)

    class _StubProvider:
        name = "garmin"

        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, metric_type, start, end):
            return [MetricReading("garmin", metric_type, datetime.combine(start, time.min), 55.0, "bpm")]

    monkeypatch.setattr("core.providers.garmin.GarminProvider", _StubProvider)

    async with Client(mcp) as client:
        await client.call_tool(
            "refetch_garmin_metric_range",
            {"metric_type": "resting_hr", "start": "2026-08-17", "end": "2026-08-17"},
        )

    conn = connect(db_path)
    checkpoint = repository.get_checkpoint(conn, "garmin", "resting_hr")
    conn.close()
    assert checkpoint == date(2026, 8, 26)  # unchanged by a backfill of an older day


def test_refetch_garmin_metric_range_rejects_start_after_end(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    _save_stub_garmin_credentials(tmp_path)

    from mcp_server.server import refetch_garmin_metric_range

    with pytest.raises(ValueError, match="must be on or before"):
        refetch_garmin_metric_range("resting_hr", "2026-08-25", "2026-08-20")


def test_sync_strava_data_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import sync_strava_data

    with pytest.raises(ValueError, match="Strava credentials not found"):
        sync_strava_data()


def test_sync_mi_fitness_data_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import sync_mi_fitness_data

    with pytest.raises(ValueError, match="Mi Fitness credentials not found"):
        sync_mi_fitness_data()


# ---------------------------------------------------------------------------
# Tonal tools
# ---------------------------------------------------------------------------


def test_sync_tonal_data_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import sync_tonal_data

    with pytest.raises(ValueError, match="Tonal credentials not found"):
        sync_tonal_data()


def test_search_tonal_movements_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import search_tonal_movements

    with pytest.raises(ValueError, match="Tonal credentials not found"):
        search_tonal_movements(query="press")


def test_get_tonal_workout_history_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import get_tonal_workout_history

    with pytest.raises(ValueError, match="Tonal credentials not found"):
        get_tonal_workout_history()


def test_get_tonal_workout_detail_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import get_tonal_workout_detail

    with pytest.raises(ValueError, match="Tonal credentials not found"):
        get_tonal_workout_detail("123")


def test_estimate_tonal_workout_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import estimate_tonal_workout

    with pytest.raises(ValueError, match="Tonal credentials not found"):
        estimate_tonal_workout([])


def test_create_tonal_workout_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import create_tonal_workout

    with pytest.raises(ValueError, match="Tonal credentials not found"):
        create_tonal_workout("Full Body", [])


def test_delete_tonal_workout_raises_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))

    from mcp_server.server import delete_tonal_workout

    with pytest.raises(ValueError, match="Tonal credentials not found"):
        delete_tonal_workout("w-1")


def _save_stub_tonal_credentials(tmp_path):
    """Write a credentials file so the tools' connected-check passes, mirroring
    test_sync_garmin_data_tool_forwards_force_full_history's setup."""
    from core.config import get_or_create_secret_key
    from core.security.credentials import CredentialStore

    secret_key_path = tmp_path / ".env"
    credentials_path = tmp_path / "tonal_credentials.enc"
    secret_key = get_or_create_secret_key(secret_key_path)
    CredentialStore(secret_key, credentials_path).save({"email": "a@example.com", "password": "x"})


class _StubTonalProvider:
    """Stand-in for TonalProvider that skips all real Tonal HTTP/auth calls,
    just like _StubProvider does for GarminProvider in the garmin sync test."""

    name = "tonal"

    def __init__(self, *args, **kwargs):
        self.hydrate_calls = []

    def search_movements(self, query=None, muscle_group=None):
        return [{"id": "m1", "name": "Bench Press", "muscle_groups": ["Chest"]}]

    def get_workout_detail(self, conn, activity_id):
        return {"total_duration_seconds": 1800, "total_volume_lbs": 5000.0, "sets": []}

    def estimate_workout(self, blocks):
        return {"estimated_duration_min": 30, "set_count": 10}

    def create_workout(self, title, blocks):
        return {"workout_id": "w-1", "title": title, "set_count": 10, "exercise_count": 2}

    def delete_workout(self, workout_id):
        return True

    def hydrate_recent_strength_sets(self, conn, since):
        self.hydrate_calls.append(since)
        return {"workouts": 1, "sets": 5}

    def sync_hydration(self, conn, start_date, end_date, force_full_history):
        """Mirrors TonalProvider.sync_hydration's checkpoint-driven wrapper
        (core/providers/tonal.py) so tests exercising sync_tonal_data's
        composition of sync_all_metrics + hydration get the same contract a
        real provider gives it: skip entirely on a full-history run, resume
        from the checkpoint day itself (not day-after) or start_date if
        there's no checkpoint yet, and never advance the checkpoint on a
        hydration failure."""
        if force_full_history:
            return "skipped (full history sync)"
        checkpoint = repository.get_checkpoint(conn, "tonal", "tonal_strength_sets")
        hydrate_since = checkpoint if checkpoint else start_date
        try:
            hydration = self.hydrate_recent_strength_sets(conn, since=hydrate_since)
            repository.set_checkpoint(conn, "tonal", "tonal_strength_sets", end_date)
            return f"{hydration['sets']} sets across {hydration['workouts']} workouts"
        except Exception as exc:
            return f"hydration failed: {exc}"


class _StubTonalClient:
    """Stand-in for TonalClient (get_tonal_workout_history talks to TonalClient
    directly, not TonalProvider, so it keeps total_volume_lbs -- see
    mcp_server/server.py's comment in get_tonal_workout_history)."""

    def __init__(self, *args, **kwargs):
        pass

    def get_activities(self, limit=10):
        return [
            {
                "activity_id": "1",
                "date": "2026-01-01T09:00:00Z",
                "title": "Full Body Workout",
                "type": "Full Body",
                "duration_seconds": 1800,
                "total_volume_lbs": 5000.0,
            }
        ][:limit]


def test_sync_tonal_data_tool_forwards_force_full_history(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    connect(tmp_path / "athlytics.db").close()
    _save_stub_tonal_credentials(tmp_path)

    captured = {}

    def fake_sync_all_metrics(conn, provider, backfill_start, end, **kwargs):
        captured.update(kwargs)
        return {"tonal_strength_score": "complete"}

    monkeypatch.setattr("core.providers.tonal.TonalProvider", _StubTonalProvider)
    monkeypatch.setattr("core.scheduler.sync.sync_all_metrics", fake_sync_all_metrics)

    from mcp_server.server import sync_tonal_data

    result = sync_tonal_data(days=7, force_full_history=True)

    assert result == {
        "tonal_strength_score": "complete",
        "tonal_strength_sets": "skipped (full history sync)",
    }
    assert captured.get("force_full_backfill") is True


def test_sync_tonal_data_tool_hydrates_strength_sets_on_incremental_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    connect(tmp_path / "athlytics.db").close()
    _save_stub_tonal_credentials(tmp_path)

    def fake_sync_all_metrics(conn, provider, backfill_start, end, **kwargs):
        return {"tonal_strength_score": "complete"}

    monkeypatch.setattr("core.providers.tonal.TonalProvider", _StubTonalProvider)
    monkeypatch.setattr("core.scheduler.sync.sync_all_metrics", fake_sync_all_metrics)

    from mcp_server.server import sync_tonal_data

    result = sync_tonal_data(days=7, force_full_history=False)

    assert result == {
        "tonal_strength_score": "complete",
        "tonal_strength_sets": "5 sets across 1 workouts",
    }


class _CapturingSinceTonalProvider(_StubTonalProvider):
    """Records every `since` a hydration call receives on a class-level list
    (instances are recreated per sync_tonal_data call, so a per-instance list
    like _StubTonalProvider's wouldn't survive to be inspected by the test)."""

    captured_since: list = []

    def hydrate_recent_strength_sets(self, conn, since):
        _CapturingSinceTonalProvider.captured_since.append(since)
        return {"workouts": 1, "sets": 5}


def test_sync_tonal_data_hydrates_from_checkpoint_day_itself_not_day_after(tmp_path, monkeypatch):
    """A sync on day D hydrates through D and sets the checkpoint to D. If the
    athlete trains again later that same day D, the next sync must still pick
    up that workout, so hydrate_since must be the checkpoint day itself, not
    checkpoint + 1 day -- unlike core/scheduler/sync.py's daily-aggregate
    checkpoint convention, upsert_strength_sets is idempotent by id, so
    re-hydrating the checkpoint day on every sync is free and correct."""
    db_path = tmp_path / "athlytics.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    checkpoint_day = date(2026, 8, 20)
    repository.set_checkpoint(conn, "tonal", "tonal_strength_sets", checkpoint_day)
    conn.close()
    _save_stub_tonal_credentials(tmp_path)

    def fake_sync_all_metrics(conn, provider, backfill_start, end, **kwargs):
        return {"tonal_strength_score": "complete"}

    _CapturingSinceTonalProvider.captured_since = []
    monkeypatch.setattr("core.providers.tonal.TonalProvider", _CapturingSinceTonalProvider)
    monkeypatch.setattr("core.scheduler.sync.sync_all_metrics", fake_sync_all_metrics)

    from mcp_server.server import sync_tonal_data

    sync_tonal_data(days=7, force_full_history=False)

    assert _CapturingSinceTonalProvider.captured_since == [checkpoint_day]


class _FailingHydrateTonalProvider(_StubTonalProvider):
    """Raises from hydrate_recent_strength_sets itself, standing in for an
    unwrapped failure in one of the pre-loop calls (get_recent_workout_set_activity
    / _movement_lookup) that hydrate_recent_strength_sets does not itself
    try/except -- from sync_tonal_data's point of view these look identical."""

    def hydrate_recent_strength_sets(self, conn, since):
        raise RuntimeError("simulated rate limit error")


def test_sync_tonal_data_isolates_hydration_failure_and_keeps_sync_all_metrics_results(tmp_path, monkeypatch):
    db_path = tmp_path / "athlytics.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()
    _save_stub_tonal_credentials(tmp_path)

    def fake_sync_all_metrics(conn, provider, backfill_start, end, **kwargs):
        return {"tonal_strength_score": "complete", "tonal_workout_volume": "complete"}

    monkeypatch.setattr("core.providers.tonal.TonalProvider", _FailingHydrateTonalProvider)
    monkeypatch.setattr("core.scheduler.sync.sync_all_metrics", fake_sync_all_metrics)

    from mcp_server.server import sync_tonal_data

    result = sync_tonal_data(days=7, force_full_history=False)

    # sync_all_metrics's already-successful results must survive the
    # hydration failure, not be discarded by a propagating exception.
    assert result["tonal_strength_score"] == "complete"
    assert result["tonal_workout_volume"] == "complete"
    assert "hydration failed" in result["tonal_strength_sets"]
    assert "simulated rate limit error" in result["tonal_strength_sets"]

    # The checkpoint must not advance past a failed hydration.
    conn = connect(db_path)
    assert repository.get_checkpoint(conn, "tonal", "tonal_strength_sets") is None
    conn.close()


def test_tonal_read_write_tools_return_provider_results(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    connect(tmp_path / "athlytics.db").close()
    _save_stub_tonal_credentials(tmp_path)
    monkeypatch.setattr("core.providers.tonal.TonalProvider", _StubTonalProvider)
    monkeypatch.setattr("core.providers.tonal_client.TonalClient", _StubTonalClient)

    from mcp_server.server import (
        create_tonal_workout,
        delete_tonal_workout,
        estimate_tonal_workout,
        get_tonal_workout_detail,
        get_tonal_workout_history,
        search_tonal_movements,
    )

    assert search_tonal_movements(query="press") == [
        {"id": "m1", "name": "Bench Press", "muscle_groups": ["Chest"]}
    ]

    assert get_tonal_workout_detail("1") == {
        "total_duration_seconds": 1800, "total_volume_lbs": 5000.0, "sets": []
    }
    assert estimate_tonal_workout([]) == {"estimated_duration_min": 30, "set_count": 10}
    assert create_tonal_workout("Full Body", []) == {
        "workout_id": "w-1", "title": "Full Body", "set_count": 10, "exercise_count": 2
    }
    assert delete_tonal_workout("w-1") is True


def test_get_tonal_workout_history_includes_volume_and_type(tmp_path, monkeypatch):
    """Regression: get_tonal_workout_history must go through TonalClient
    directly (not TonalProvider.fetch_activities -> Activity, which has no
    total_volume_lbs field) so per-workout volume and type survive."""
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    connect(tmp_path / "athlytics.db").close()
    _save_stub_tonal_credentials(tmp_path)
    monkeypatch.setattr("core.providers.tonal_client.TonalClient", _StubTonalClient)

    from mcp_server.server import get_tonal_workout_history

    history = get_tonal_workout_history(limit=5)

    assert history == [
        {
            "activity_id": "1",
            "date": "2026-01-01T09:00:00Z",
            "title": "Full Body Workout",
            "type": "Full Body",
            "duration_seconds": 1800,
            "total_volume_lbs": 5000.0,
        }
    ]


def test_get_movement_history_returns_chronological_sets_for_unambiguous_match(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    conn = connect(tmp_path / "athlytics.db")
    from core.storage.models import StrengthSet
    older = StrengthSet(
        id="tonal:w1:0", activity_id="tonal:w1", movement_id="mv-bench", movement_name="Bench Press",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=100.0, volume_lbs=800.0, one_rep_max=130.0,
        max_power_watts=400.0, rom_inches=18.0, struggling_score=0.3, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 7, 1, 8, 0),
    )
    newer = StrengthSet(
        id="tonal:w2:0", activity_id="tonal:w2", movement_id="mv-bench", movement_name="Bench Press",
        set_index=0, is_warm_up=False, reps=6, weight_lbs=115.0, volume_lbs=690.0, one_rep_max=140.0,
        max_power_watts=420.0, rom_inches=18.5, struggling_score=0.6, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 8, 1, 8, 0),
    )
    repository.upsert_strength_sets(conn, [older, newer])
    conn.close()

    from mcp_server.server import get_movement_history

    result = get_movement_history("bench")

    assert len(result) == 2
    assert result[0]["date"] == "2026-08-01T08:00:00"  # newest first
    assert result[0]["one_rep_max"] == 140.0
    assert result[1]["one_rep_max"] == 130.0


def test_get_movement_history_returns_candidates_when_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    conn = connect(tmp_path / "athlytics.db")
    from core.storage.models import StrengthSet
    bench = StrengthSet(
        id="tonal:w1:0", activity_id="tonal:w1", movement_id="mv-bench", movement_name="Bench Press",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=100.0, volume_lbs=800.0, one_rep_max=130.0,
        max_power_watts=400.0, rom_inches=18.0, struggling_score=0.3, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 7, 1, 8, 0),
    )
    close_grip = StrengthSet(
        id="tonal:w2:0", activity_id="tonal:w2", movement_id="mv-cgbp", movement_name="Close Grip Bench Press",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=90.0, volume_lbs=720.0, one_rep_max=115.0,
        max_power_watts=380.0, rom_inches=18.0, struggling_score=0.4, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 7, 5, 8, 0),
    )
    repository.upsert_strength_sets(conn, [bench, close_grip])
    conn.close()

    from mcp_server.server import get_movement_history

    result = get_movement_history("bench")

    assert {r["movement_id"] for r in result} == {"mv-bench", "mv-cgbp"}


def test_get_movement_history_returns_empty_for_unknown_movement(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    connect(tmp_path / "athlytics.db").close()

    from mcp_server.server import get_movement_history

    assert get_movement_history("deadlift") == []


def test_get_muscle_group_volume_aggregates_and_sorts_busiest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    conn = connect(tmp_path / "athlytics.db")
    from core.storage.models import StrengthSet
    chest = StrengthSet(
        id="tonal:w1:0", activity_id="tonal:w1", movement_id="mv-bench", movement_name="Bench Press",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=100.0, volume_lbs=800.0, one_rep_max=130.0,
        max_power_watts=400.0, rom_inches=18.0, struggling_score=0.3, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 7, 10, 8, 0),
    )
    quads = StrengthSet(
        id="tonal:w2:0", activity_id="tonal:w2", movement_id="mv-squat", movement_name="Squat",
        set_index=0, is_warm_up=False, reps=8, weight_lbs=225.0, volume_lbs=1800.0, one_rep_max=280.0,
        max_power_watts=500.0, rom_inches=20.0, struggling_score=0.3, side="Both",
        created_at=datetime(2026, 1, 1, 12, 0), occurred_at=datetime(2026, 7, 12, 8, 0),
    )
    repository.upsert_strength_sets(conn, [chest, quads])
    repository.replace_strength_set_muscle_groups(conn, chest.id, ["Chest"])
    repository.replace_strength_set_muscle_groups(conn, quads.id, ["Quads"])
    conn.close()

    from mcp_server.server import get_muscle_group_volume

    result = get_muscle_group_volume("2026-07-01", "2026-07-31")

    assert [r["muscle_group"] for r in result] == ["Quads", "Chest"]
    assert result[0]["total_volume_lbs"] == 1800.0


def test_get_muscle_group_volume_empty_for_range_with_no_hydrated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    connect(tmp_path / "athlytics.db").close()

    from mcp_server.server import get_muscle_group_volume

    assert get_muscle_group_volume("2020-01-01", "2020-01-31") == []


def _load_fixture(name: str):
    """Load a Garmin fixture from tests/fixtures/garmin/{name}.json"""
    fixture_dir = Path(__file__).resolve().parent.parent / "fixtures" / "garmin"
    return json.loads((fixture_dir / f"{name}.json").read_text())


@pytest.mark.anyio
async def test_get_sleep_detail_not_hydrated(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_sleep_detail", {"date": "2026-01-01"})

    assert result.is_error is not True
    body = result.structured_content or (json.loads(result.content[0].text) if result.content else None)
    assert body == {"status": "not_hydrated", "date": "2026-01-01"}


@pytest.mark.anyio
async def test_get_sleep_detail_returns_full_detail(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)

    from core.providers.garmin import GarminProvider

    fixture = _load_fixture("get_sleep_data")
    session = GarminProvider._parse_sleep_session(fixture, source_id_prefix="garmin")
    segments = GarminProvider._parse_sleep_stage_segments(fixture, session_id=session.id)
    moments = GarminProvider._parse_sleep_restless_moments(fixture, session_id=session.id)
    repository.upsert_sleep_session(conn, session)
    repository.replace_sleep_stage_segments(conn, session.id, segments)
    repository.replace_sleep_restless_moments(conn, session.id, moments)
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_sleep_detail", {"date": "2026-09-15"})

    assert result.is_error is not True
    body = result.structured_content or (json.loads(result.content[0].text) if result.content else None)
    assert body["overall_score"] == 76.0
    assert body["duration_qualifier"] == "FAIR"
    assert len(body["stage_timeline"]) == 4
    assert body["stage_timeline"][0]["stage"] == "light"
    # stage_timeline must report local wall-clock times, not UTC, so it's
    # consistent with bedtime_local for the same instant: the fixture's
    # sleepStartTimestampGMT/Local pair implies a -4h offset, so the first
    # segment's 03:46:52 UTC start becomes 23:46:52 the prior local day,
    # matching bedtime_local ("23:46") rather than contradicting it.
    assert "start_utc" not in body["stage_timeline"][0]
    assert body["bedtime_local"] == "23:46"
    assert body["stage_timeline"][0]["start_local"] == "2026-09-14T23:46:52"
    assert body["stage_timeline"][0]["end_local"] == "2026-09-14T23:47:52"
    assert len(body["restless_moments"]) == 2
    assert "time_utc" not in body["restless_moments"][0]
    assert body["restless_moments"][0]["time_local"] == "2026-09-14T23:58:52"
    assert body["restless_moments"][1]["time_local"] == "2026-09-15T00:49:52"
    # Verify zero nap time is preserved as 0.0, not None (fixture has napTimeSeconds: 0)
    assert body["nap_time_minutes"] == 0.0


def test_median_clock_time_handles_midnight_wrap():
    from mcp_server.server import _median_clock_time_local

    # Bedtimes straddling midnight: 23:50 and 00:10. A naive mean of the raw
    # hour:minute numbers would land near 12:00 (wrong); anchor-space median
    # must land at 00:00 (the actual midpoint of that 20-minute window).
    result = _median_clock_time_local([time(23, 50), time(0, 10)], anchor_hour=18)
    assert result == "00:00"

    # Non-wrapping case: sanity check the anchor logic doesn't break ordinary times.
    result2 = _median_clock_time_local([time(22, 0), time(23, 0), time(0, 30)], anchor_hour=18)
    assert result2 == "23:00"


def _make_sleep_pattern_session(session_id, d, total_sleep_seconds, overall_score, duration_qualifier, sleep_start_local):
    from core.storage.models import SleepSession

    return SleepSession(
        id=session_id, calendar_date=d,
        sleep_start_utc=None, sleep_end_utc=None,
        sleep_start_local=sleep_start_local, sleep_end_local=None,
        total_sleep_seconds=total_sleep_seconds, nap_time_seconds=0.0,
        deep_sleep_seconds=total_sleep_seconds * 0.25, light_sleep_seconds=total_sleep_seconds * 0.5,
        rem_sleep_seconds=total_sleep_seconds * 0.25, awake_sleep_seconds=0.0,
        unmeasurable_sleep_seconds=0.0, awake_count=0, restless_moments_count=10,
        avg_sleep_stress=20.0, avg_heart_rate=60.0, avg_overnight_hrv=25.0,
        avg_respiration=15.0, lowest_respiration=12.0, highest_respiration=20.0,
        overall_score=overall_score, overall_score_qualifier="FAIR",
        duration_qualifier=duration_qualifier, stress_qualifier="FAIR",
        awake_count_qualifier="EXCELLENT", restlessness_qualifier="EXCELLENT",
        rem_percentage=25.0, rem_percentage_qualifier="EXCELLENT",
        light_percentage=50.0, light_percentage_qualifier="EXCELLENT",
        deep_percentage=25.0, deep_percentage_qualifier="EXCELLENT",
        sleep_need_baseline_minutes=470, sleep_need_actual_minutes=470,
        sleep_need_feedback="NO_CHANGE", score_feedback="NEUTRAL",
        score_insight="NONE", score_personalized_insight="NONE",
        created_at=datetime(2026, 9, 15, 12, 0, 0),
    )


@pytest.mark.anyio
async def test_get_sleep_pattern_aggregates_correctly(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    repository.upsert_sleep_session(conn, _make_sleep_pattern_session(
        "garmin:2026-09-13", date(2026, 9, 13), 20880.0, 58.0, "POOR", datetime(2026, 9, 12, 23, 50),
    ))
    repository.upsert_sleep_session(conn, _make_sleep_pattern_session(
        "garmin:2026-09-14", date(2026, 9, 14), 23472.0, 78.0, "FAIR", datetime(2026, 9, 14, 0, 32),
    ))
    repository.upsert_sleep_session(conn, _make_sleep_pattern_session(
        "garmin:2026-09-15", date(2026, 9, 15), 24780.0, 76.0, "FAIR", datetime(2026, 9, 14, 23, 46),
    ))
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_sleep_pattern", {"start_date": "2026-09-13", "end_date": "2026-09-15"})

    assert result.is_error is not True
    body = result.structured_content or (json.loads(result.content[0].text) if result.content else None)
    assert body["nights_with_data"] == 3
    assert body["duration_qualifier_counts"] == {"POOR": 1, "FAIR": 2}
    assert body["avg_overall_score"] == round((58.0 + 78.0 + 76.0) / 3, 2)
    assert len(body["worst_nights"]) == 3
    assert body["worst_nights"][0]["date"] == "2026-09-13"  # lowest score first


@pytest.mark.anyio
async def test_get_sleep_pattern_empty_range(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_sleep_pattern", {"start_date": "2020-01-01", "end_date": "2020-01-02"})

    assert result.is_error is not True
    body = result.structured_content or (json.loads(result.content[0].text) if result.content else None)
    assert body["nights_with_data"] == 0
    assert body["avg_duration_hours"] is None


@pytest.mark.anyio
async def test_get_sleep_pattern_includes_zero_duration_and_zero_need_nights(tmp_path, monkeypatch):
    """A session with total_sleep_seconds == 0 or sleep_need_baseline_minutes == 0 is a real
    zero value (not "unknown") and must be counted in the averages, not silently dropped by a
    truthy check -- the same class of bug already fixed for get_sleep_detail in 19bebc3."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)

    zero_session = dataclasses.replace(
        _make_sleep_pattern_session(
            "garmin:2026-09-13", date(2026, 9, 13), 0.0, 50.0, "POOR", datetime(2026, 9, 12, 23, 0),
        ),
        sleep_need_baseline_minutes=0,
    )
    repository.upsert_sleep_session(conn, zero_session)
    repository.upsert_sleep_session(conn, _make_sleep_pattern_session(
        "garmin:2026-09-14", date(2026, 9, 14), 7200.0, 80.0, "FAIR", datetime(2026, 9, 13, 23, 0),
    ))
    conn.close()

    async with Client(mcp) as client:
        result = await client.call_tool("get_sleep_pattern", {"start_date": "2026-09-13", "end_date": "2026-09-14"})

    assert result.is_error is not True
    body = result.structured_content or (json.loads(result.content[0].text) if result.content else None)
    assert body["nights_with_data"] == 2
    # avg of [0.0, 2.0] hours is 1.0 -- a truthy-check bug would exclude the zero night
    # entirely and report 2.0 instead.
    assert body["avg_duration_hours"] == 1.0
    # avg of [0, 470/60] minutes-as-hours -- a truthy-check bug would exclude the zero
    # need entirely and report 7.83 instead.
    assert body["avg_sleep_need_target_hours"] == round((0 + 470 / 60.0) / 2, 2)
    assert body["avg_deficit_hours"] == round(body["avg_sleep_need_target_hours"] - body["avg_duration_hours"], 2)
    # worst_nights is sorted by overall_score ascending, so the zero-duration
    # session (score 50.0) is worst_nights[0] -- its duration_hours must be
    # 0.0, not None. Same truthy-check bug class as above, but in the
    # worst_nights list-comprehension branch rather than the aggregate avg.
    assert body["worst_nights"][0]["date"] == "2026-09-13"
    assert body["worst_nights"][0]["duration_hours"] == 0.0


def test_refetch_garmin_sleep_detail_range_rejects_start_after_end(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(tmp_path / "athlytics.db"))
    _save_stub_garmin_credentials(tmp_path)

    from mcp_server.server import refetch_garmin_sleep_detail_range

    with pytest.raises(ValueError, match="must be on or before"):
        refetch_garmin_sleep_detail_range("2026-08-25", "2026-08-20")


@pytest.mark.anyio
async def test_refetch_garmin_sleep_detail_range_does_not_touch_checkpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    repository.set_checkpoint(conn, "garmin", "garmin_sleep_detail", date(2026, 9, 1))
    conn.close()
    _save_stub_garmin_credentials(tmp_path)

    class _StubProvider:
        name = "garmin"

        def __init__(self, *args, **kwargs):
            pass

        def hydrate_recent_sleep(self, conn, since, until=None):
            return {"nights": 3, "segments": 12, "restless_moments": 7}

    monkeypatch.setattr("core.providers.garmin.GarminProvider", _StubProvider)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "refetch_garmin_sleep_detail_range", {"start": "2026-09-10", "end": "2026-09-12"}
        )

    assert result.is_error is not True
    body = result.structured_content or (json.loads(result.content[0].text) if result.content else None)
    assert body == {"nights": 3, "segments": 12, "restless_moments": 7}

    conn = connect(db_path)
    checkpoint = repository.get_checkpoint(conn, "garmin", "garmin_sleep_detail")
    conn.close()
    assert checkpoint == date(2026, 9, 1)  # unchanged


@pytest.mark.anyio
async def test_refetch_garmin_sleep_detail_range_passes_explicit_bounds(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()
    _save_stub_garmin_credentials(tmp_path)

    captured = {}

    class _StubProvider:
        name = "garmin"

        def __init__(self, *args, **kwargs):
            pass

        def hydrate_recent_sleep(self, conn, since, until=None):
            captured["since"] = since
            captured["until"] = until
            return {"nights": 0, "segments": 0, "restless_moments": 0}

    monkeypatch.setattr("core.providers.garmin.GarminProvider", _StubProvider)

    async with Client(mcp) as client:
        await client.call_tool(
            "refetch_garmin_sleep_detail_range", {"start": "2026-09-10", "end": "2026-09-12"}
        )

    assert captured["since"] == date(2026, 9, 10)
    assert captured["until"] == date(2026, 9, 12)
