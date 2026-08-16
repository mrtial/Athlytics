from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.data_sources import SUPPORTED_PROVIDERS, connect_garmin
from app.dependencies import require_admin_page
from core.providers.garmin import GarminAuthError, GarminMfaRequired, complete_garmin_mfa

router = APIRouter()


@router.post("/api/data-sources/{provider}/connect")
def connect_data_source(
    provider: str,
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    conn=Depends(require_admin_page),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unsupported data source provider: {provider!r}")

    credential_store = request.app.state.credential_store
    token_cache_dir = request.app.state.token_cache_dir
    garmin_client_factory = request.app.state.garmin_client_factory

    try:
        connect_garmin(credential_store, token_cache_dir, email, password, garmin_client_factory=garmin_client_factory)
    except GarminMfaRequired as exc:
        # exc.client is a live, partially-authenticated session object --
        # keep it in server memory (this app is single-admin, so app.state
        # is a fine home for it) until the follow-up request with the code.
        request.app.state.pending_garmin_mfa = {"client": exc.client, "client_state": exc.client_state}
        return RedirectResponse(url="/onboarding/connect/mfa", status_code=303)
    except GarminAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request.app.state.sync_scheduler.trigger()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/api/data-sources/garmin/mfa")
def submit_garmin_mfa(
    request: Request,
    mfa_code: str = Form(...),
    conn=Depends(require_admin_page),
):
    pending = request.app.state.pending_garmin_mfa
    if not pending:
        raise HTTPException(
            status_code=400,
            detail="No pending Garmin MFA challenge. Start over at /onboarding/connect.",
        )

    try:
        complete_garmin_mfa(
            pending["client"], pending["client_state"], mfa_code, request.app.state.token_cache_dir
        )
    except GarminAuthError as exc:
        # Keep pending state so the user can retry the code without
        # re-entering email/password and restarting the whole login.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request.app.state.pending_garmin_mfa = None
    request.app.state.sync_scheduler.trigger()
    return RedirectResponse(url="/dashboard", status_code=303)
