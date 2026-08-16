from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.dependencies import onboarding_status, require_admin_page
from app.settings import PERSONA_METRIC_TYPES, get_persona, get_theme
from app.widgets import build_dashboard_widgets

router = APIRouter()


@router.get("/dashboard")
def dashboard_page(request: Request, conn=Depends(require_admin_page)):
    status = onboarding_status(conn, request.app.state.credential_store)
    if status != "complete":
        return RedirectResponse(url=f"/onboarding/{status}", status_code=303)

    persona = get_persona(conn)
    theme = get_theme(conn)
    metric_types = PERSONA_METRIC_TYPES[persona]
    widgets = build_dashboard_widgets(conn, metric_types)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"widgets": widgets, "persona": persona, "theme": theme, "authenticated": True},
    )
