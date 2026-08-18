from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.dependencies import require_admin_page
from app.settings import (
    DEFAULT_PERSONA,
    DEFAULT_SKIN,
    DEFAULT_THEME,
    DEFAULT_UNIT,
    PERSONAS,
    SKINS,
    THEMES,
    UNITS,
    generate_api_token,
    get_api_token,
    get_athlete_age,
    get_athlete_name,
    get_persona,
    get_skin,
    get_theme,
    get_unit,
    set_api_token,
    set_athlete_profile,
    set_persona,
    set_skin,
    set_theme,
    set_unit,
)
from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES
from core.providers.garmin import GARMIN_METRIC_TYPES
from core.storage.repository import get_source_priority, has_synced_data

router = APIRouter()


def _settings_context(conn, request, *, theme, persona_error=None, theme_error=None, unit_error=None, skin_error=None, profile_error=None):
    garmin_connected = request.app.state.credential_store.load() is not None
    apple_connected = has_synced_data(conn, "apple_health")
    strava_connected = request.app.state.strava_credential_store.load() is not None
    overlapping_metric_types = []
    if garmin_connected and apple_connected:
        overlapping_metric_types = sorted(set(GARMIN_METRIC_TYPES) & set(APPLE_HEALTH_METRIC_TYPES))

    api_token = get_api_token(conn)
    if api_token is None:
        api_token = generate_api_token()
        set_api_token(conn, api_token)
    apple_health_upload_url = str(request.base_url).rstrip("/") + "/api/data-sources/apple-health/import"

    return {
        "authenticated": True,
        "personas": PERSONAS,
        "themes": THEMES,
        "skins": SKINS,
        "units": UNITS,
        "current_persona": get_persona(conn) or DEFAULT_PERSONA,
        "current_theme": theme,
        "theme": theme,
        "current_skin": get_skin(conn) or DEFAULT_SKIN,
        "skin": get_skin(conn) or DEFAULT_SKIN,
        "current_unit": get_unit(conn) or DEFAULT_UNIT,
        "athlete_name": get_athlete_name(conn),
        "athlete_age": get_athlete_age(conn),
        "persona_error": persona_error,
        "theme_error": theme_error,
        "unit_error": unit_error,
        "skin_error": skin_error,
        "profile_error": profile_error,
        "garmin_connected": garmin_connected,
        "apple_health_connected": apple_connected,
        "strava_connected": strava_connected,
        "overlapping_metric_types": overlapping_metric_types,
        "source_priority": {mt: get_source_priority(conn, mt) or "garmin" for mt in overlapping_metric_types},
        "api_token": api_token,
        "apple_health_upload_url": apple_health_upload_url,
    }


@router.get("/settings")
def settings_page(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_settings_context(conn, request, theme=theme),
    )


@router.post("/settings/profile")
def settings_update_profile(
    request: Request,
    athlete_name: str = Form(""),
    athlete_age: str = Form(""),
    conn=Depends(require_admin_page),
):
    set_athlete_profile(conn, athlete_name, athlete_age)
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/unit")
def settings_update_unit(
    request: Request,
    unit: str = Form(...),
    conn=Depends(require_admin_page),
):
    templates = request.app.state.templates
    try:
        set_unit(conn, unit)
    except ValueError as exc:
        theme = get_theme(conn) or DEFAULT_THEME
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_settings_context(conn, request, theme=theme, unit_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/persona")
def settings_update_persona(request: Request, persona: str = Form(...), conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    try:
        set_persona(conn, persona)
    except ValueError as exc:
        theme = get_theme(conn) or DEFAULT_THEME
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_settings_context(conn, request, theme=theme, persona_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/theme")
def settings_update_theme(request: Request, theme: str = Form(...), conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    try:
        set_theme(conn, theme)
    except ValueError as exc:
        current_theme = get_theme(conn) or DEFAULT_THEME
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_settings_context(conn, request, theme=current_theme, theme_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/skin")
def settings_update_skin(request: Request, skin: str = Form(...), conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    try:
        set_skin(conn, skin)
    except ValueError as exc:
        theme = get_theme(conn) or DEFAULT_THEME
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_settings_context(conn, request, theme=theme, skin_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/api-token/regenerate")
def settings_regenerate_api_token(request: Request, conn=Depends(require_admin_page)):
    set_api_token(conn, generate_api_token())
    return RedirectResponse(url="/settings", status_code=303)
