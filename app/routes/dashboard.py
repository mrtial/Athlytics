from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.dependencies import onboarding_status, require_admin_page
from app.settings import (
    DEFAULT_SKIN,
    PERSONA_METRIC_TYPES,
    get_athlete_age,
    get_athlete_name,
    get_persona,
    get_skin,
    get_theme,
    get_unit,
)
from app.widgets import build_dashboard_widgets, build_recent_activities
from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES
from core.providers.garmin import GARMIN_METRIC_TYPES
from core.storage import repository

router = APIRouter()

PROVIDER_METRIC_TYPES = {"garmin": GARMIN_METRIC_TYPES, "apple_health": APPLE_HEALTH_METRIC_TYPES}


@router.get("/dashboard")
def dashboard_page(request: Request, conn=Depends(require_admin_page)):
    status = onboarding_status(conn, request.app.state.credential_store, request.app.state.strava_credential_store)
    if status != "complete":
        return RedirectResponse(url=f"/onboarding/{status}", status_code=303)

    persona = get_persona(conn)
    theme = get_theme(conn)
    skin = get_skin(conn) or DEFAULT_SKIN
    unit = get_unit(conn) or "km"
    athlete_name = get_athlete_name(conn)
    athlete_age = get_athlete_age(conn)

    connected_sources = set()
    if request.app.state.credential_store.load() is not None:
        connected_sources.add("garmin")
    if repository.has_synced_data(conn, "apple_health"):
        connected_sources.add("apple_health")

    metric_types = [
        mt for mt in PERSONA_METRIC_TYPES[persona]
        if any(mt in PROVIDER_METRIC_TYPES[s] for s in connected_sources)
    ]
    widgets = build_dashboard_widgets(conn, metric_types)
    activities = build_recent_activities(conn, unit=unit, limit=10)


    # If unit is miles, calculate display values for distance metrics
    first_name = athlete_name.split()[0] if athlete_name else "Athlete"
    today_formatted = date.today().strftime("%a, %d %b")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "widgets": widgets,
            "activities": activities,
            "persona": persona,
            "theme": theme,
            "skin": skin,
            "unit": unit,
            "athlete_name": athlete_name,
            "athlete_first_name": first_name,
            "athlete_age": athlete_age,
            "today_formatted": today_formatted,
            "authenticated": True,
            "active_page": "dashboard",
        },
    )
