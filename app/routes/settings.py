from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.dependencies import require_admin_page
from app.settings import (
    DEFAULT_PERSONA,
    DEFAULT_THEME,
    PERSONAS,
    THEMES,
    get_persona,
    get_theme,
    set_persona,
    set_theme,
)

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "authenticated": True,
            "personas": PERSONAS,
            "themes": THEMES,
            "current_persona": get_persona(conn) or DEFAULT_PERSONA,
            "current_theme": get_theme(conn) or DEFAULT_THEME,
            "persona_error": None,
            "theme_error": None,
        },
    )


@router.post("/settings/persona")
def settings_update_persona(request: Request, persona: str = Form(...), conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    try:
        set_persona(conn, persona)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "authenticated": True,
                "personas": PERSONAS,
                "themes": THEMES,
                "current_persona": get_persona(conn) or DEFAULT_PERSONA,
                "current_theme": get_theme(conn) or DEFAULT_THEME,
                "persona_error": str(exc),
                "theme_error": None,
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
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "authenticated": True,
                "personas": PERSONAS,
                "themes": THEMES,
                "current_persona": get_persona(conn) or DEFAULT_PERSONA,
                "current_theme": get_theme(conn) or DEFAULT_THEME,
                "persona_error": None,
                "theme_error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url="/settings", status_code=303)
