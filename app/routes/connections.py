from fastapi import APIRouter, Depends, Request

from app.dependencies import require_admin_page
from app.settings import (
    DEFAULT_SKIN,
    DEFAULT_THEME,
    get_athlete_age,
    get_athlete_name,
    get_persona,
    get_skin,
    get_theme,
)
from core.providers.registry import PROVIDER_REGISTRY

router = APIRouter()


@router.get("/connections")
def connections_page(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    athlete_name = get_athlete_name(conn)
    athlete_age = get_athlete_age(conn)
    persona = get_persona(conn)

    providers = [
        {
            "id": p.id,
            "display_name": p.display_name,
            "flow_type": p.flow_type,
            "connected": p.is_connected(conn, request.app.state),
        }
        for p in PROVIDER_REGISTRY
    ]

    return templates.TemplateResponse(
        request=request,
        name="connections.html",
        context={
            "authenticated": True,
            "active_page": "connections",
            "theme": theme,
            "skin": skin,
            "athlete_name": athlete_name,
            "athlete_age": athlete_age,
            "persona": persona,
            "providers": providers,
        },
    )
