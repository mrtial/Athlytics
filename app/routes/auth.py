from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import authenticate_admin
from app.dependencies import get_conn
from app.session import SESSION_COOKIE_NAME, SESSION_LIFETIME, create_session, delete_session
from app.settings import DEFAULT_THEME, get_theme

router = APIRouter()


def set_session_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=request.app.state.secure_cookies,
        max_age=int(SESSION_LIFETIME.total_seconds()),
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)


@router.get("/login")
def login_form(request: Request, conn=Depends(get_conn)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": None, "theme": theme}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    conn=Depends(get_conn),
):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    if not authenticate_admin(conn, username, password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid username or password.", "theme": theme},
            status_code=401,
        )

    token = create_session(conn)
    response = RedirectResponse(url="/dashboard", status_code=303)
    set_session_cookie(response, request, token)
    return response


@router.post("/logout")
def logout(request: Request, conn=Depends(get_conn)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(conn, token)
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response
