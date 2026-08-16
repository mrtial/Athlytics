from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.dependencies import onboarding_status, require_admin_page
from app.settings import (
    PERSONA_METRIC_TYPES,
    get_athlete_age,
    get_athlete_name,
    get_persona,
    get_theme,
    get_unit,
)
from app.widgets import build_dashboard_widgets

router = APIRouter()


@router.get("/dashboard")
def dashboard_page(request: Request, conn=Depends(require_admin_page)):
    status = onboarding_status(conn, request.app.state.credential_store)
    if status != "complete":
        return RedirectResponse(url=f"/onboarding/{status}", status_code=303)

    persona = get_persona(conn)
    theme = get_theme(conn)
    unit = get_unit(conn)
    athlete_name = get_athlete_name(conn)
    athlete_age = get_athlete_age(conn)

    metric_types = PERSONA_METRIC_TYPES[persona]
    widgets = build_dashboard_widgets(conn, metric_types)

    # If unit is miles, calculate display values for distance metrics
    first_name = athlete_name.split()[0] if athlete_name else "Athlete"
    today_formatted = date.today().strftime("%a, %d %b")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "widgets": widgets,
            "persona": persona,
            "theme": theme,
            "unit": unit,
            "athlete_name": athlete_name,
            "athlete_first_name": first_name,
            "athlete_age": athlete_age,
            "today_formatted": today_formatted,
            "authenticated": True,
        },
    )
