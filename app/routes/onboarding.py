from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import create_admin, create_admin_without_password
from app.dependencies import get_conn, onboarding_progress, require_admin_page
from app.qr import apple_health_shortcut_qr_svg
from app.session import SESSION_COOKIE_NAME, SESSION_LIFETIME, create_session
from app.settings import (
    DEFAULT_SKIN,
    DEFAULT_THEME,
    generate_api_token,
    get_api_token,
    get_athlete_dob,
    get_athlete_name,
    get_skin,
    get_theme,
    set_api_token,
    set_athlete_profile,
    set_persona,
    set_theme,
)
from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES
from core.providers.registry import PROVIDER_REGISTRY

router = APIRouter()


@router.get("/onboarding/admin")
def onboarding_admin_form(request: Request, conn=Depends(get_conn)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    return templates.TemplateResponse(
        request=request,
        name="onboarding_admin.html",
        context={
            "error": None,
            "theme": theme,
            "skin": skin,
            "onboarding_steps": onboarding_progress(conn, request.app.state, "admin"),
        },
    )


@router.post("/onboarding/admin")
def onboarding_admin_submit(
    request: Request,
    protect: str = Form("yes"),
    username: str | None = Form(None),
    password: str | None = Form(None),
    conn=Depends(get_conn),
):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    try:
        if protect == "no":
            create_admin_without_password(conn)
        else:
            if not username or not password:
                raise ValueError("username and password are required")
            create_admin(conn, username, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_admin.html",
            context={
                "error": str(exc),
                "theme": theme,
                "skin": skin,
                "onboarding_steps": onboarding_progress(conn, request.app.state, "admin"),
            },
            status_code=400,
        )

    token = create_session(conn)
    response = RedirectResponse(url="/onboarding/profile", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=request.app.state.secure_cookies,
        max_age=int(SESSION_LIFETIME.total_seconds()),
    )
    return response


@router.get("/onboarding/profile")
def onboarding_profile_form(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    today = date.today()
    return templates.TemplateResponse(
        request=request,
        name="onboarding_profile.html",
        context={
            "error": None,
            "theme": theme,
            "skin": skin,
            "athlete_name": get_athlete_name(conn),
            "athlete_dob": get_athlete_dob(conn),
            "dob_min": today.replace(year=today.year - 120).isoformat(),
            "dob_max": today.isoformat(),
            "onboarding_steps": onboarding_progress(conn, request.app.state, "profile"),
        },
    )


@router.post("/onboarding/profile")
def onboarding_profile_submit(
    request: Request,
    athlete_name: str = Form(...),
    athlete_dob: str = Form(""),
    conn=Depends(require_admin_page),
):
    set_athlete_profile(conn, athlete_name, athlete_dob)
    return RedirectResponse(url="/onboarding/persona", status_code=303)


@router.get("/onboarding/persona")
def onboarding_persona_form(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    return templates.TemplateResponse(
        request=request,
        name="onboarding_persona.html",
        context={
            "error": None,
            "theme": theme,
            "skin": skin,
            "onboarding_steps": onboarding_progress(conn, request.app.state, "persona"),
        },
    )


@router.post("/onboarding/persona")
def onboarding_persona_submit(
    request: Request, persona: str = Form(...), conn=Depends(require_admin_page)
):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    try:
        set_persona(conn, persona)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_persona.html",
            context={
                "error": str(exc),
                "theme": theme,
                "skin": skin,
                "onboarding_steps": onboarding_progress(conn, request.app.state, "persona"),
            },
            status_code=400,
        )
    return RedirectResponse(url="/onboarding/theme", status_code=303)


@router.get("/onboarding/theme")
def onboarding_theme_form(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    return templates.TemplateResponse(
        request=request,
        name="onboarding_theme.html",
        context={
            "error": None,
            "theme": theme,
            "skin": skin,
            "onboarding_steps": onboarding_progress(conn, request.app.state, "theme"),
        },
    )


@router.post("/onboarding/theme")
def onboarding_theme_submit(
    request: Request, theme: str = Form(...), conn=Depends(require_admin_page)
):
    templates = request.app.state.templates
    try:
        set_theme(conn, theme)
    except ValueError as exc:
        current_theme = get_theme(conn) or DEFAULT_THEME
        skin = get_skin(conn) or DEFAULT_SKIN
        return templates.TemplateResponse(
            request=request,
            name="onboarding_theme.html",
            context={
                "error": str(exc),
                "theme": current_theme,
                "skin": skin,
                "onboarding_steps": onboarding_progress(conn, request.app.state, "theme"),
            },
            status_code=400,
        )
    return RedirectResponse(url="/onboarding/connect", status_code=303)


@router.get("/onboarding/connect")
def onboarding_connect_form(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN

    providers = sorted(
        (
            {
                "id": p.id,
                "display_name": p.display_name,
                "flow_type": p.flow_type,
                "connected": p.is_connected(conn, request.app.state),
            }
            for p in PROVIDER_REGISTRY
        ),
        key=lambda p: not p["connected"],
    )

    api_token = get_api_token(conn)
    if api_token is None:
        api_token = generate_api_token()
        set_api_token(conn, api_token)
    apple_health_upload_url = str(request.base_url).rstrip("/") + "/api/data-sources/apple-health/import"
    apple_health_shortcut_qr = apple_health_shortcut_qr_svg(api_token, apple_health_upload_url)
    apple_health_metrics_url = str(request.base_url).rstrip("/") + "/api/data-sources/apple-health/metrics"
    apple_health_metrics_qr = apple_health_shortcut_qr_svg(api_token, apple_health_metrics_url)

    return templates.TemplateResponse(
        request=request,
        name="onboarding_connect.html",
        context={
            "error": None,
            "theme": theme,
            "skin": skin,
            "providers": providers,
            "id_prefix": "onboarding",
            "api_token": api_token,
            "apple_health_upload_url": apple_health_upload_url,
            "apple_health_shortcut_qr": apple_health_shortcut_qr,
            "apple_health_metrics_url": apple_health_metrics_url,
            "apple_health_metrics_qr": apple_health_metrics_qr,
            "apple_health_metric_types": sorted(APPLE_HEALTH_METRIC_TYPES),
            "apple_health_success_redirect": "/dashboard",
            "strava_import_success_redirect": "/dashboard",
            "mi_fitness_success_url": "/dashboard",
            "sync_in_progress": request.app.state.sync_scheduler.is_syncing(),
            "currently_syncing_source": request.app.state.currently_syncing_source,
            "sync_metric_progress": request.app.state.sync_metric_progress,
            "onboarding_steps": onboarding_progress(conn, request.app.state, "connect"),
        },
    )


@router.get("/onboarding/connect/mfa")
def onboarding_connect_mfa_form(request: Request, conn=Depends(require_admin_page)):
    if not request.app.state.pending_garmin_mfa:
        return RedirectResponse(url="/onboarding/connect", status_code=303)
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    return templates.TemplateResponse(
        request=request,
        name="onboarding_mfa.html",
        context={
            "error": None,
            "theme": theme,
            "skin": skin,
            "onboarding_steps": onboarding_progress(conn, request.app.state, "connect"),
        },
    )
