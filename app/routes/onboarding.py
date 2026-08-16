from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import create_admin
from app.dependencies import get_conn
from app.session import SESSION_COOKIE_NAME, SESSION_LIFETIME, create_session

router = APIRouter()


@router.get("/onboarding/admin")
def onboarding_admin_form(request: Request, conn=Depends(get_conn)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request, name="onboarding_admin.html", context={"error": None}
    )


@router.post("/onboarding/admin")
def onboarding_admin_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    conn=Depends(get_conn),
):
    templates = request.app.state.templates
    try:
        create_admin(conn, username, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_admin.html",
            context={"error": str(exc)},
            status_code=400,
        )

    token = create_session(conn)
    response = RedirectResponse(url="/onboarding/persona", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=request.app.state.secure_cookies,
        max_age=int(SESSION_LIFETIME.total_seconds()),
    )
    return response
