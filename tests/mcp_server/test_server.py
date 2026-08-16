from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from core.storage import repository
from core.storage.db import connect
from core.storage.models import CoachNote, MetricReading, Target, TrainingPlan
from mcp_server.server import DB_PATH_ENV_VAR, _db_path, mcp


def test_server_instance_is_an_mcp_server():
    assert isinstance(mcp, MCPServer)
    assert mcp.name == "Athlytics"


def test_db_path_respects_environment_override(monkeypatch, tmp_path):
    custom_db = tmp_path / "custom.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(custom_db))
    assert _db_path() == custom_db


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
    conn.close()

    async with Client(mcp) as client:
        # get_report
        res_rep = await client.call_tool("get_report", {"id": rep_id})
        assert res_rep.structured_content["title"] == "Report 1"

        # get_targets
        res_tar = await client.call_tool("get_targets", {"status": "active"})
        assert len(res_tar.structured_content["result"]) == 1
        assert res_tar.structured_content["result"][0]["metric_type"] == "hrv"

        # get_training_plans
        res_plan = await client.call_tool("get_training_plans", {"status": "active"})
        assert len(res_plan.structured_content["result"]) == 1
        assert res_plan.structured_content["result"][0]["title"] == "Base"

        # get_coach_notes
        res_notes = await client.call_tool("get_coach_notes", {"limit": 5})
        assert len(res_notes.structured_content["result"]) == 1
        assert res_notes.structured_content["result"][0]["category"] == "feeling"


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

        # update_plan_status
        res_up = await client.call_tool(
            "update_plan_status", {"plan_id": plan_id, "status": "paused"}
        )
        assert res_up.is_error is not True
        assert res_up.structured_content["status"] == "paused"

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


@pytest.mark.anyio
async def test_sync_garmin_data_tool_raises_when_credentials_not_found(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    connect(db_path).close()

    async with Client(mcp) as client:
        result = await client.call_tool("sync_garmin_data", {"days": 7})

    assert result.is_error is True
