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
