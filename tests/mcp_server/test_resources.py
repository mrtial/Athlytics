from datetime import date, datetime, time, timedelta

import pytest
from mcp import Client

from core.storage import repository
from core.storage.db import connect
from core.storage.models import CoachNote, MetricReading, Target, TrainingPlan
from mcp_server.server import mcp


@pytest.mark.anyio
async def test_dynamic_resources_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ATHLYTICS_DB_PATH", str(db_path))
    conn = connect(db_path)
    today = date.today()
    now = datetime.combine(today, time(12, 0))

    # Populate 7 days of readings
    for d in range(7):
        day = today - timedelta(days=d)
        dt = datetime.combine(day, time.min)
        repository.upsert_readings(
            conn,
            [
                MetricReading("garmin", "resting_hr", dt, 50.0, "bpm"),
                MetricReading("garmin", "hrv", dt, 65.0, "ms"),
                MetricReading("garmin", "sleep_score", dt, 85.0, "score"),
                MetricReading("garmin", "training_load", dt, 120.0, "load"),
            ],
        )

    # Active target, plan, and coach note
    repository.save_target(
        conn,
        Target("t-1", "resting_hr", 52.0, "lte", "daily", today, None, "active", "Keep RHR low", now),
    )
    repository.save_training_plan(
        conn,
        TrainingPlan(
            "p-1",
            "Base 10k",
            "Aerobic base",
            today,
            today + timedelta(days=60),
            '{"phase": "Base", "week": 1, "today_workout": "45min Zone 2 Run"}',
            "active",
            now,
        ),
    )
    repository.save_coach_note(
        conn,
        CoachNote("n-1", today, "injury", "Left hamstring feeling tight", None, now),
    )
    conn.close()

    async with Client(mcp) as client:
        # Resource 1: athlete snapshot
        res_snap = await client.read_resource("athlytics://athlete/snapshot")
        assert "7-Day Health Snapshot" in res_snap.contents[0].text
        assert "resting_hr" in res_snap.contents[0].text

        # Resource 2: training state
        res_train = await client.read_resource("athlytics://training/current-state")
        assert "Base 10k" in res_train.contents[0].text
        assert "45min Zone 2 Run" in res_train.contents[0].text

        # Resource 3: coach context
        res_ctx = await client.read_resource("athlytics://coach/context")
        assert "Left hamstring feeling tight" in res_ctx.contents[0].text
