from datetime import date, datetime, time, timedelta

import pytest
from mcp import Client

from core.storage import repository
from core.storage.db import connect
from core.storage.models import MetricReading
from mcp_server.server import mcp


@pytest.mark.anyio
async def test_full_ai_coaching_workflow(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()

    # 1. Backfill 14 days of realistic baseline data
    for d in range(14):
        day = today - timedelta(days=d)
        dt = datetime.combine(day, time.min)
        repository.upsert_readings(
            conn,
            [
                MetricReading("garmin", "resting_hr", dt, 49.0 + (d % 2), "bpm"),
                MetricReading("garmin", "hrv", dt, 70.0 - (d % 3), "ms"),
                MetricReading("garmin", "activity_distance", dt, 8.0, "km"),
                MetricReading("garmin", "sleep_score", dt, 88.0, "score"),
            ],
        )
    conn.close()

    async with Client(mcp) as client:
        # Step A: AI opens session and reads living context snapshot
        snapshot_res = await client.read_resource("athlytics://athlete/snapshot")
        assert "7-Day Health Snapshot" in snapshot_res.contents[0].text

        # Step B: AI checks trend
        trend_res = await client.call_tool("get_trend", {"metric_type": "resting_hr", "window": 7})
        assert trend_res.structured_content["metric_type"] == "resting_hr"
        assert trend_res.structured_content["current"]["average"] is not None

        # Step C: AI sets target for athlete
        target_res = await client.call_tool(
            "set_target",
            {
                "metric_type": "activity_distance",
                "target_value": 50.0,
                "operator": "gte",
                "target_window": "weekly_sum",
                "start_date": today.isoformat(),
                "notes": "Weekly volume goal of 50km",
            },
        )
        assert target_res.is_error is not True
        target_id = target_res.structured_content["id"]

        # Step D: AI saves training plan
        plan_json_str = (
            '{"phases": [{"name": "Base", "weeks": 4}, {"name": "Build", "weeks": 4}]}'
        )
        plan_res = await client.call_tool(
            "save_training_plan",
            {
                "title": "Half Marathon Sub-90",
                "goal_description": "Target 1:29:59 half marathon",
                "start_date": today.isoformat(),
                "target_date": (today + timedelta(days=56)).isoformat(),
                "plan_json": plan_json_str,
            },
        )
        assert plan_res.is_error is not True
        plan_id = plan_res.structured_content["id"]

        # Step E: AI logs coach observation note
        note_res = await client.call_tool(
            "log_coach_note",
            {
                "date": today.isoformat(),
                "category": "milestone",
                "note": "Agreed on 8-week Sub-90 Half Marathon plan.",
                "tags": ["sub-90", "plan-kickoff"],
            },
        )
        assert note_res.is_error is not True

        # Step F: Verify living training state resource updates dynamically
        training_state_res = await client.read_resource("athlytics://training/current-state")
        assert "Half Marathon Sub-90" in training_state_res.contents[0].text
        assert "activity_distance" in training_state_res.contents[0].text

        # Step G: Verify queries return persisted records
        targets_list = await client.call_tool("get_targets", {"status": "active"})
        assert len(targets_list.structured_content["result"]) == 1
        assert targets_list.structured_content["result"][0]["id"] == target_id

        plans_list = await client.call_tool("get_training_plans", {"status": "active"})
        assert len(plans_list.structured_content["result"]) == 1
        assert plans_list.structured_content["result"][0]["id"] == plan_id

        notes_list = await client.call_tool("get_coach_notes", {"limit": 5})
        assert len(notes_list.structured_content["result"]) == 1
        assert notes_list.structured_content["result"][0]["category"] == "milestone"
