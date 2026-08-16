from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.dependencies import get_conn, onboarding_status
from app.session import SESSION_COOKIE_NAME, is_valid_session

router = APIRouter()


@router.get("/")
def root(request: Request, conn=Depends(get_conn)):
    status = onboarding_status(conn, request.app.state.credential_store)
    if status != "complete":
        return RedirectResponse(url=f"/onboarding/{status}", status_code=303)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and is_valid_session(conn, token):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)
