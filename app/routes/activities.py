from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.dependencies import is_password_protected, onboarding_status, require_admin_page
from app.settings import (
    DEFAULT_SKIN,
    get_athlete_age,
    get_athlete_name,
    get_persona,
    get_skin,
    get_theme,
    get_unit,
)
from app.widgets import build_recent_activities
from core.providers.registry import PROVIDER_REGISTRY

router = APIRouter()


@router.get("/activities")
def activities_page(request: Request, conn=Depends(require_admin_page)):
    status = onboarding_status(conn, request.app.state)
    # See dashboard_page's comment: "connect" still renders the page, with
    # an empty state pointing at Connections, rather than redirecting away.
    if status not in ("connect", "complete"):
        return RedirectResponse(url=f"/onboarding/{status}", status_code=303)

    has_connected_source = any(p.is_connected(conn, request.app.state) for p in PROVIDER_REGISTRY)

    persona = get_persona(conn)
    theme = get_theme(conn)
    skin = get_skin(conn) or DEFAULT_SKIN
    unit = get_unit(conn) or "km"
    athlete_name = get_athlete_name(conn)
    athlete_age = get_athlete_age(conn)
    first_name = athlete_name.split()[0] if athlete_name else "Athlete"
    today_formatted = date.today().strftime("%a, %d %b")

    activities = build_recent_activities(conn, unit=unit, limit=100)

    # Compute overall activity summary stats from DB
    row = conn.execute("SELECT COUNT(*), SUM(duration_seconds), SUM(distance_meters), SUM(calories) FROM activity").fetchone()
    all_count = row[0] if row and row[0] else len(activities)
    all_duration_sec = row[1] if row and row[1] else 0.0
    all_distance_m = row[2] if row and row[2] else 0.0
    all_calories = row[3] if row and row[3] else 0.0

    all_hours = int(all_duration_sec // 3600)
    all_mins = int((all_duration_sec % 3600) // 60)
    total_time_formatted = f"{all_hours}h {all_mins}m" if all_hours > 0 else f"{all_mins}m"

    if unit == "mi":
        all_distance_formatted = f"{(all_distance_m * 0.000621371):,.1f} mi"
    else:
        all_distance_formatted = f"{(all_distance_m / 1000.0):,.1f} km"

    summary_stats = {
        "total_count": all_count,
        "total_distance_formatted": all_distance_formatted,
        "total_time_formatted": total_time_formatted,
        "total_calories_formatted": f"{int(all_calories):,} kcal" if all_calories else "—",
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="activities.html",
        context={
            "activities": activities,
            "summary_stats": summary_stats,
            "persona": persona,
            "theme": theme,
            "skin": skin,
            "unit": unit,
            "athlete_name": athlete_name,
            "athlete_first_name": first_name,
            "athlete_age": athlete_age,
            "today_formatted": today_formatted,
            "has_connected_source": has_connected_source,
            "authenticated": True,
            "password_protected": is_password_protected(conn),
            "active_page": "activities",
        },
    )
