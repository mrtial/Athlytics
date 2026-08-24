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
from core.providers.registry import PROVIDER_REGISTRY

router = APIRouter()


@router.get("/dashboard")
def dashboard_page(request: Request, conn=Depends(require_admin_page)):
    status = onboarding_status(conn, request.app.state)
    # "connect" (admin/profile/persona/theme done, no data source yet) still
    # renders the page -- the dashboard itself is the right place to show
    # that empty state and point at Connections, rather than yanking the
    # athlete away from a page they deliberately navigated to. Every earlier
    # step is a real prerequisite the page can't render without, so those
    # still redirect.
    if status not in ("connect", "complete"):
        return RedirectResponse(url=f"/onboarding/{status}", status_code=303)

    persona = get_persona(conn)
    theme = get_theme(conn)
    skin = get_skin(conn) or DEFAULT_SKIN
    unit = get_unit(conn) or "km"
    athlete_name = get_athlete_name(conn)
    athlete_age = get_athlete_age(conn)

    connected_metric_types: set[str] = set()
    has_connected_source = False
    for provider in PROVIDER_REGISTRY:
        if provider.is_connected(conn, request.app.state):
            has_connected_source = True
            connected_metric_types.update(provider.metric_types)

    metric_types = [mt for mt in PERSONA_METRIC_TYPES[persona] if mt in connected_metric_types]
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
            "has_connected_source": has_connected_source,
            "authenticated": True,
            "active_page": "dashboard",
        },
    )
