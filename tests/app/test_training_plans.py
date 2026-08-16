import json
from datetime import date, datetime

from app.routes.training_plans import (
    _format_target_value,
    _goal_pace_label,
    _operator_symbol,
    _parse_plan_data,
    _stat_row,
    _timeline,
    _week_table,
    _weekly_rhythm,
    _window_label,
)
from core.storage.models import Target, TrainingPlan


def test_operator_symbol_maps_known_operators():
    assert _operator_symbol("gte") == "≥"
    assert _operator_symbol("lte") == "≤"
    assert _operator_symbol("eq") == "="


def test_operator_symbol_falls_back_to_raw_value_for_unknown_operator():
    assert _operator_symbol("weird") == "weird"


def test_window_label_maps_known_windows():
    assert _window_label("daily") == "per day"
    assert _window_label("weekly_sum") == "per week"
    assert _window_label("weekly_avg") == "weekly avg"
    assert _window_label("by_date") == "by target date"


def test_format_target_value_formats_race_predictor_seconds_as_hms():
    assert _format_target_value("race_predictor_marathon", 14400.0, "mi") == "4:00:00"
    assert _format_target_value("race_predictor_5k", 1380.0, "mi") == "23:00"


def test_format_target_value_converts_distance_to_miles_when_unit_is_mi():
    assert _format_target_value("activity_distance", 48.0, "mi") == "29.8 mi"


def test_format_target_value_keeps_distance_in_km_when_unit_is_km():
    assert _format_target_value("activity_distance", 48.0, "km") == "48.0 km"


def test_format_target_value_falls_back_to_plain_number_for_other_metrics():
    assert _format_target_value("resting_hr", 55.0, "mi") == "55"
    assert _format_target_value("resting_hr", 55.5, "mi") == "55.5"


def test_parse_plan_data_returns_dict_for_valid_json():
    assert _parse_plan_data('{"race": "Rome"}') == {"race": "Rome"}


def test_parse_plan_data_returns_none_for_malformed_json():
    assert _parse_plan_data("not json") is None


def test_parse_plan_data_returns_none_for_non_object_json():
    assert _parse_plan_data("[1, 2, 3]") is None


def test_goal_pace_label_divides_goal_time_by_marathon_distance_in_miles():
    # 4:00:00 marathon / 26.2188 mi ≈ 9:09/mi
    assert _goal_pace_label("sub-4:00:00", "mi") == "9:09 / mi"


def test_goal_pace_label_divides_goal_time_by_marathon_distance_in_km():
    assert _goal_pace_label("sub-4:00:00", "km") == "5:41 / km"


def test_goal_pace_label_returns_none_when_goal_has_no_parseable_time():
    assert _goal_pace_label("run fast", "mi") is None


def test_goal_pace_label_returns_none_for_empty_goal():
    assert _goal_pace_label(None, "mi") is None
    assert _goal_pace_label("", "mi") is None


def test_stat_row_includes_core_stats_and_goal_pace():
    plan_data = {
        "total_weeks": 30,
        "training_days_per_week": 5,
        "goal": "sub-4:00:00",
        "baseline": {"marathon_predictor_hms": "4:21:57"},
    }

    stats = _stat_row(plan_data, date(2027, 3, 14), date(2026, 8, 17), "mi")

    labels = {s["label"]: s["value"] for s in stats}
    assert labels["Race day"] == "Mar 14, 2027"
    assert labels["Start date"] == "Aug 17, 2026"
    assert labels["Duration"] == "30 weeks"
    assert labels["Training days"] == "5 / week"
    assert labels["Current predictor"] == "4:21:57"
    assert labels["Goal pace"] == "9:09 / mi"
    assert next(s for s in stats if s["label"] == "Goal pace")["accent"] is True


def test_stat_row_omits_predictor_and_pace_when_data_missing():
    stats = _stat_row({}, date(2027, 3, 14), date(2026, 8, 17), "mi")

    labels = [s["label"] for s in stats]
    assert "Current predictor" not in labels
    assert "Goal pace" not in labels


def test_weekly_rhythm_marks_rest_and_long_run_days():
    plan_data = {
        "rest_days": ["Mon", "Fri"],
        "weekly_rhythm": {
            "Mon": "Rest",
            "Tue": "Easy run",
            "Wed": "Workout",
            "Thu": "Easy run",
            "Fri": "Rest",
            "Sat": "Short shakeout",
            "Sun": "Long run",
        },
    }

    rows = _weekly_rhythm(plan_data)

    assert [r["day"] for r in rows] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert rows[0]["is_rest"] is True
    assert rows[1]["is_rest"] is False
    assert rows[6]["is_long"] is True
    assert rows[1]["is_long"] is False


def test_weekly_rhythm_skips_days_missing_from_rhythm_dict():
    plan_data = {"rest_days": [], "weekly_rhythm": {"Mon": "Rest"}}

    rows = _weekly_rhythm(plan_data)

    assert [r["day"] for r in rows] == ["Mon"]


def _plan_data_with_phases():
    return {
        "rest_days": ["Mon", "Fri"],
        "phases": [
            {
                "phase": 1,
                "name": "Base Building",
                "subphases": [
                    {"name": "Foundation", "weeks": "1-4", "start_date": "2026-08-17", "end_date": "2026-09-13"},
                    {"name": "Base Peak", "weeks": "5-8", "start_date": "2026-09-14", "end_date": "2026-10-11"},
                ],
            },
            {
                "phase": 2,
                "name": "Marathon-Specific Training",
                "subphases": [
                    {"name": "Build", "weeks": "9-12", "start_date": "2026-10-12", "end_date": "2026-11-08"},
                    {"name": "Taper", "weeks": "13-14", "start_date": "2026-11-09", "end_date": "2026-11-22"},
                ],
            },
        ],
    }


def test_timeline_builds_one_segment_per_subphase_with_week_counts():
    timeline = _timeline(_plan_data_with_phases())

    assert [s["name"] for s in timeline["segments"]] == ["Foundation", "Base Peak", "Build", "Taper"]
    assert [s["week_count"] for s in timeline["segments"]] == [4, 4, 4, 2]
    assert [s["color_class"] for s in timeline["segments"]] == ["phase-1", "phase-1", "phase-2", "taper"]


def test_timeline_legend_merges_consecutive_same_color_segments():
    timeline = _timeline(_plan_data_with_phases())

    assert [entry["color_class"] for entry in timeline["legend"]] == ["phase-1", "phase-2", "taper"]
    assert timeline["legend"][0]["date_range_label"] == "Aug 17 – Oct 11, 2026"
    assert timeline["legend"][2]["label"] == "Taper"


def _plan_data_with_schedule():
    data = _plan_data_with_phases()
    data["weekly_schedule"] = [
        {"week": 1, "phase": "Foundation", "start_date": "2026-08-17", "tue": 2, "wed": 2, "wed_note": "Easy", "thu": 2, "sat": 1, "sun": 5, "total": 12, "flag": None},
        {"week": 2, "phase": "Foundation", "start_date": "2026-08-24", "tue": 2.5, "wed": 2.5, "thu": 2, "sat": 1, "sun": 6, "total": 14, "flag": "cutback"},
        {"week": 5, "phase": "Base Peak", "start_date": "2026-09-14", "tue": 3, "wed": 3, "thu": 2.5, "sat": 1.5, "sun": 7, "total": 17, "flag": None},
        {"week": 14, "phase": "Taper", "start_date": "2026-11-16", "tue": 3, "wed": 2, "thu": 2, "sat": 1, "sun": 26.2, "sun_note": "RACE DAY", "total_training": 8, "flag": "race"},
    ]
    return data


def test_week_table_uses_non_rest_days_as_columns_in_week_order():
    result = _week_table(_plan_data_with_schedule())

    assert result["training_days"] == ["Tue", "Wed", "Thu", "Sat", "Sun"]


def test_week_table_inserts_divider_row_on_phase_change_only():
    result = _week_table(_plan_data_with_schedule())

    kinds_and_phases = [(r["kind"], r.get("phase_name")) for r in result["rows"]]
    assert kinds_and_phases == [
        ("divider", "Foundation"),
        ("week", None),
        ("week", None),
        ("divider", "Base Peak"),
        ("week", None),
        ("divider", "Taper"),
        ("week", None),
    ]


def test_week_table_divider_includes_subphase_date_range():
    result = _week_table(_plan_data_with_schedule())

    dividers = [r for r in result["rows"] if r["kind"] == "divider"]
    assert dividers[0]["date_range_label"] == "Aug 17 – Sep 13, 2026"


def test_week_table_week_row_has_cells_matching_training_days():
    result = _week_table(_plan_data_with_schedule())

    first_week = next(r for r in result["rows"] if r["kind"] == "week")
    assert [c["day"] for c in first_week["cells"]] == ["Tue", "Wed", "Thu", "Sat", "Sun"]
    assert [c["value"] for c in first_week["cells"]] == [2, 2, 2, 1, 5]
    assert first_week["cells"][1]["note"] == "Easy"
    assert first_week["dates_label"] == "Aug 17 – Aug 23"
    assert first_week["total"] == 12
    assert first_week["flag_label"] is None


def test_week_table_falls_back_to_total_training_for_race_week():
    result = _week_table(_plan_data_with_schedule())

    race_week = next(r for r in result["rows"] if r["kind"] == "week" and r.get("is_race_week"))
    assert race_week["total"] == 8
    assert race_week["total_extra_label"] == "+ race"
    assert race_week["flag_label"] == "Race week"


def _target(**overrides):
    defaults = dict(
        id="t1",
        metric_type="activity_distance",
        target_value=48.0,
        operator="gte",
        target_window="weekly_sum",
        start_date=date(2026, 8, 17),
        end_date=date(2026, 11, 8),
        status="active",
        notes=None,
        created_at=datetime(2026, 8, 16, 22, 26),
    )
    defaults.update(overrides)
    return Target(**defaults)


def _plan(**overrides):
    defaults = dict(
        id="p1",
        title="Rome Marathon 2027",
        goal_description="30-week build",
        start_date=date(2026, 8, 17),
        target_date=date(2027, 3, 14),
        plan_json=json.dumps(_plan_data_with_schedule()),
        status="active",
        created_at=datetime(2026, 8, 16, 22, 26),
    )
    defaults.update(overrides)
    return TrainingPlan(**defaults)


def test_training_plans_route_requires_admin_login(client):
    response = client.get("/training-plans", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_training_plans_route_shows_empty_state_with_no_data(client):
    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})

    response = client.get("/training-plans")

    assert response.status_code == 200
    assert "No training plans yet" in response.text


def test_training_plans_route_renders_saved_plan(app, client):
    # Note: the Goals/targets section was intentionally removed from this
    # page's template -- targets_view is still computed and passed to the
    # template (kept for a likely future use), but nothing currently
    # renders it, so this only asserts the plan itself shows up.
    from core.storage import repository
    from core.storage.db import connect
    from app.db import ensure_app_schema

    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    repository.save_training_plan(conn, _plan())
    conn.close()

    response = client.get("/training-plans")

    assert response.status_code == 200
    assert "Rome Marathon 2027" in response.text


def test_training_plans_route_renders_plan_without_weekly_schedule_gracefully(app, client):
    from core.storage import repository
    from core.storage.db import connect
    from app.db import ensure_app_schema

    client.post("/onboarding/admin", data={"username": "athlete", "password": "hunter2hunter2"})
    conn = connect(app.state.db_path)
    ensure_app_schema(conn)
    repository.save_training_plan(conn, _plan(plan_json='{"race": "Rome"}'))
    conn.close()

    response = client.get("/training-plans")

    assert response.status_code == 200
    assert "Rome Marathon 2027" in response.text
