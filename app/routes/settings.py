from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.dependencies import require_admin_page
from app.settings import (
    DEFAULT_PERSONA,
    DEFAULT_THEME,
    DEFAULT_UNIT,
    PERSONAS,
    THEMES,
    UNITS,
    get_athlete_age,
    get_athlete_name,
    get_persona,
    get_theme,
    get_unit,
    set_athlete_profile,
    set_persona,
    set_theme,
    set_unit,
)

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "authenticated": True,
            "personas": PERSONAS,
            "themes": THEMES,
            "units": UNITS,
            "current_persona": get_persona(conn) or DEFAULT_PERSONA,
            "current_theme": theme,
            "theme": theme,
            "current_unit": get_unit(conn) or DEFAULT_UNIT,
            "athlete_name": get_athlete_name(conn),
            "athlete_age": get_athlete_age(conn),
            "persona_error": None,
            "theme_error": None,
            "unit_error": None,
            "profile_error": None,
        },
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
            context={
                "authenticated": True,
                "personas": PERSONAS,
                "themes": THEMES,
                "units": UNITS,
                "current_persona": get_persona(conn) or DEFAULT_PERSONA,
                "current_theme": theme,
                "theme": theme,
                "current_unit": get_unit(conn) or DEFAULT_UNIT,
                "athlete_name": get_athlete_name(conn),
                "athlete_age": get_athlete_age(conn),
                "persona_error": None,
                "theme_error": None,
                "unit_error": str(exc),
                "profile_error": None,
            },
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
            context={
                "authenticated": True,
                "personas": PERSONAS,
                "themes": THEMES,
                "units": UNITS,
                "current_persona": get_persona(conn) or DEFAULT_PERSONA,
                "current_theme": theme,
                "theme": theme,
                "current_unit": get_unit(conn) or DEFAULT_UNIT,
                "athlete_name": get_athlete_name(conn),
                "athlete_age": get_athlete_age(conn),
                "persona_error": str(exc),
                "theme_error": None,
                "unit_error": None,
                "profile_error": None,
            },
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
            context={
                "authenticated": True,
                "personas": PERSONAS,
                "themes": THEMES,
                "units": UNITS,
                "current_persona": get_persona(conn) or DEFAULT_PERSONA,
                "current_theme": current_theme,
                "theme": current_theme,
                "current_unit": get_unit(conn) or DEFAULT_UNIT,
                "athlete_name": get_athlete_name(conn),
                "athlete_age": get_athlete_age(conn),
                "persona_error": None,
                "theme_error": str(exc),
                "unit_error": None,
                "profile_error": None,
            },
            status_code=400,
        )
    return RedirectResponse(url="/settings", status_code=303)
