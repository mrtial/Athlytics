from fastapi import APIRouter, Depends, Request

from app.dependencies import is_password_protected, require_admin_page
from app.settings import DEFAULT_SKIN, get_athlete_age, get_athlete_name, get_persona, get_skin, get_theme
from core.storage import repository

router = APIRouter()


@router.get("/coach")
def coach_page(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    notes = repository.get_coach_notes(conn, limit=50)
    notes_view = [
        {"note": note, "date_label": note.date.strftime("%b %d, %Y")}
        for note in notes
    ]

    return templates.TemplateResponse(
        request=request,
        name="coach.html",
        context={
            "authenticated": True,
            "password_protected": is_password_protected(conn),
            "theme": get_theme(conn),
            "skin": get_skin(conn) or DEFAULT_SKIN,
            "athlete_name": get_athlete_name(conn),
            "athlete_age": get_athlete_age(conn),
            "persona": get_persona(conn),
            "notes_view": notes_view,
            "active_page": "coach",
        },
    )
