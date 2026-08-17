import json
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.dependencies import require_admin_page
from app.settings import DEFAULT_SKIN, get_athlete_age, get_athlete_name, get_persona, get_skin, get_theme, get_unit
from core.storage import repository

router = APIRouter()

_OPERATOR_SYMBOLS = {"gte": "≥", "lte": "≤", "eq": "="}
_WINDOW_LABELS = {
    "daily": "per day",
    "weekly_sum": "per week",
    "weekly_avg": "weekly avg",
    "by_date": "by target date",
}
_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_FLAG_LABELS = {
    "cutback": "Cutback",
    "key": "Key week",
    "taper": "Taper",
    "race": "Race week",
    "transition": "Transition",
}
_VERDICT_LABELS = {
    "on_track": "On track",
    "ahead": "Ahead",
    "behind": "Behind",
    "caution": "Caution",
}
_VERDICT_CLASSES = {
    "on_track": "on-track",
    "ahead": "ahead",
    "behind": "behind",
    "caution": "caution",
}
_MARATHON_MILES = 26.2188
_MARATHON_KM = 42.195


def _operator_symbol(operator: str) -> str:
    return _OPERATOR_SYMBOLS.get(operator, operator)


def _window_label(window: str) -> str:
    return _WINDOW_LABELS.get(window, window.replace("_", " "))


def _format_target_value(metric_type: str, value: float, unit: str) -> str:
    if metric_type.startswith("race_predictor"):
        total = int(value)
        hours, minutes, seconds = total // 3600, (total % 3600) // 60, total % 60
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
    if metric_type == "activity_distance":
        return f"{value * 0.621371:.1f} mi" if unit == "mi" else f"{value:.1f} km"
    return f"{value:g}"


def _parse_plan_data(plan_json: str) -> dict | None:
    try:
        data = json.loads(plan_json)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _goal_pace_label(goal: str | None, unit: str) -> str | None:
    """'sub-4:00:00' -> '9:09 / mi' -- the goal marathon finish time divided
    by the marathon distance, matching the "Goal pace" stat. Returns None
    when goal isn't a recognizable "sub-H:MM:SS" time (nothing to divide).
    """
    if not goal:
        return None
    match = re.search(r"(\d+):(\d{2}):(\d{2})", goal)
    if not match:
        return None
    hours, minutes, seconds = (int(g) for g in match.groups())
    total_seconds = hours * 3600 + minutes * 60 + seconds
    distance = _MARATHON_MILES if unit == "mi" else _MARATHON_KM
    pace_seconds = total_seconds / distance
    pace_minutes, pace_remainder = divmod(round(pace_seconds), 60)
    unit_label = "mi" if unit == "mi" else "km"
    return f"{pace_minutes}:{pace_remainder:02d} / {unit_label}"


def _stat_row(plan_data: dict, plan_target_date: date, plan_start_date: date, unit: str) -> list[dict]:
    baseline = plan_data.get("baseline") or {}
    stats = [
        {"label": "Race day", "value": plan_target_date.strftime("%b %d, %Y")},
        {"label": "Start date", "value": plan_start_date.strftime("%b %d, %Y")},
        {"label": "Duration", "value": f"{plan_data.get('total_weeks', '—')} weeks"},
        {"label": "Training days", "value": f"{plan_data.get('training_days_per_week', '—')} / week"},
    ]
    if baseline.get("marathon_predictor_hms"):
        stats.append({"label": "Current predictor", "value": baseline["marathon_predictor_hms"]})
    goal_pace = _goal_pace_label(plan_data.get("goal"), unit)
    if goal_pace:
        stats.append({"label": "Goal pace", "value": goal_pace, "accent": True})
    return stats


def _weekly_rhythm(plan_data: dict) -> list[dict]:
    rhythm = plan_data.get("weekly_rhythm") or {}
    rest_days = set(plan_data.get("rest_days") or [])
    rows = []
    for day in _DAY_ORDER:
        role = rhythm.get(day)
        if role is None:
            continue
        rows.append(
            {
                "day": day,
                "role": role,
                "is_rest": day in rest_days,
                "is_long": "long run" in role.lower(),
            }
        )
    return rows


def _subphase_color_class(phase_number: int, subphase_name: str) -> str:
    if subphase_name.lower() == "taper":
        return "taper"
    return "phase-1" if phase_number == 1 else "phase-2"


def _timeline(plan_data: dict) -> dict:
    phases = plan_data.get("phases") or []
    segments = []
    for phase in phases:
        for sub in phase.get("subphases") or []:
            weeks = str(sub.get("weeks", ""))
            start_str, _, end_str = weeks.partition("-")
            try:
                week_count = (int(end_str.strip()) - int(start_str.strip()) + 1) if end_str else 1
            except ValueError:
                week_count = 1
            segments.append(
                {
                    "name": sub.get("name"),
                    "weeks_label": weeks,
                    "week_count": max(week_count, 1),
                    "color_class": _subphase_color_class(phase.get("phase"), sub.get("name", "")),
                    "group_label": phase.get("name") if sub.get("name", "").lower() != "taper" else "Taper",
                    "start_date": sub.get("start_date"),
                    "end_date": sub.get("end_date"),
                }
            )

    legend = []
    for seg in segments:
        if legend and legend[-1]["color_class"] == seg["color_class"]:
            legend[-1]["end_date"] = seg["end_date"]
        else:
            legend.append(
                {
                    "color_class": seg["color_class"],
                    "label": seg["group_label"],
                    "start_date": seg["start_date"],
                    "end_date": seg["end_date"],
                }
            )
    for entry in legend:
        start = date.fromisoformat(entry["start_date"]) if entry["start_date"] else None
        end = date.fromisoformat(entry["end_date"]) if entry["end_date"] else None
        entry["date_range_label"] = (
            f"{start:%b %d} – {end:%b %d, %Y}" if start and end else ""
        )

    return {"segments": segments, "legend": legend}


def _subphase_date_ranges(plan_data: dict) -> dict[str, tuple[str, str]]:
    lookup = {}
    for phase in plan_data.get("phases") or []:
        for sub in phase.get("subphases") or []:
            if sub.get("name"):
                lookup[sub["name"]] = (sub.get("start_date"), sub.get("end_date"))
    return lookup


def _week_date_range_label(start_iso: str) -> str:
    start = date.fromisoformat(start_iso)
    end = start + timedelta(days=6)
    return f"{start:%b %d} – {end:%b %d}"


def _week_table(plan_data: dict, today: date | None = None) -> dict:
    today = today or date.today()
    schedule = plan_data.get("weekly_schedule") or []
    rest_days = set(plan_data.get("rest_days") or [])
    training_days = [day for day in _DAY_ORDER if day not in rest_days]
    day_keys = [day[:3].lower() for day in training_days]
    subphase_dates = _subphase_date_ranges(plan_data)

    rows = []
    previous_phase = None
    for week in schedule:
        phase_name = week.get("phase")
        if phase_name != previous_phase:
            start_iso, end_iso = subphase_dates.get(phase_name, (None, None))
            date_range_label = ""
            if start_iso and end_iso:
                date_range_label = f"{date.fromisoformat(start_iso):%b %d} – {date.fromisoformat(end_iso):%b %d, %Y}"
            rows.append({"kind": "divider", "phase_name": phase_name, "date_range_label": date_range_label})
            previous_phase = phase_name

        flag = week.get("flag")
        cells = []
        for day, key in zip(training_days, day_keys):
            value = week.get(key)
            cells.append({"day": day, "value": value, "note": week.get(f"{key}_note")})

        week_start = date.fromisoformat(week["start_date"]) if week.get("start_date") else None
        is_current = bool(week_start and week_start <= today <= week_start + timedelta(days=6))

        # Weekly check-ins write an "actual" object onto the week entry after
        # reviewing that week (see marathon-weekly-checkin skill) — it's absent
        # until a check-in has happened for that week, which is expected for
        # future weeks and simply renders as no progress info.
        actual = week.get("actual") or {}
        verdict = actual.get("verdict")

        rows.append(
            {
                "kind": "week",
                "week": week.get("week"),
                "dates_label": _week_date_range_label(week["start_date"]) if week.get("start_date") else "",
                "cells": cells,
                "total": week.get("total", week.get("total_training")),
                "total_extra_label": "+ race" if flag == "race" else None,
                "flag": flag,
                "flag_label": _FLAG_LABELS.get(flag),
                "is_race_week": flag == "race",
                "is_current": is_current,
                "actual_total": actual.get("total"),
                "actual_note": actual.get("note"),
                "verdict_label": _VERDICT_LABELS.get(verdict),
                "verdict_class": _VERDICT_CLASSES.get(verdict),
            }
        )

    return {"training_days": training_days, "rows": rows}


@router.get("/training-plans")
def training_plans_page(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    unit = get_unit(conn)

    today = date.today()
    plans = repository.get_training_plans(conn)
    plans_view = []
    for plan in plans:
        plan_data = _parse_plan_data(plan.plan_json)
        view = {
            "plan": plan,
            "start_label": plan.start_date.strftime("%b %d, %Y"),
            "target_label": plan.target_date.strftime("%b %d, %Y"),
            "data": plan_data,
        }
        if plan_data and plan_data.get("weekly_schedule"):
            view["stats"] = _stat_row(plan_data, plan.target_date, plan.start_date, unit)
            view["rhythm"] = _weekly_rhythm(plan_data)
            view["timeline"] = _timeline(plan_data)
            view["week_table"] = _week_table(plan_data, today)
        plans_view.append(view)

    targets = repository.get_targets(conn)
    targets_view = [
        {
            "target": target,
            "metric_label": target.metric_type.replace("_", " ").title(),
            "operator_symbol": _operator_symbol(target.operator),
            "value_label": _format_target_value(target.metric_type, target.target_value, unit),
            "window_label": _window_label(target.target_window),
            "date_range_label": (
                f"{target.start_date.strftime('%b %d, %Y')} → "
                f"{target.end_date.strftime('%b %d, %Y') if target.end_date else 'ongoing'}"
            ),
        }
        for target in targets
    ]

    return templates.TemplateResponse(
        request=request,
        name="training_plans.html",
        context={
            "authenticated": True,
            "theme": get_theme(conn),
            "skin": get_skin(conn) or DEFAULT_SKIN,
            "athlete_name": get_athlete_name(conn),
            "athlete_age": get_athlete_age(conn),
            "persona": get_persona(conn),
            "plans_view": plans_view,
            "targets_view": targets_view,
            "active_page": "training_plans",
        },
    )


@router.post("/training-plans/{plan_id}/status")
def update_training_plan_status(
    plan_id: str,
    status: str = Form(...),
    conn=Depends(require_admin_page),
):
    repository.update_plan_status(conn, plan_id, status)
    return RedirectResponse(url="/training-plans", status_code=303)


@router.post("/training-plans/{plan_id}/delete")
def delete_training_plan(
    plan_id: str,
    conn=Depends(require_admin_page),
):
    repository.delete_training_plan(conn, plan_id)
    return RedirectResponse(url="/training-plans", status_code=303)
