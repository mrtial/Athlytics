"""OAuth authorize + callback routes for connecting a Strava account.

Unlike Garmin's `/api/data-sources/{provider}/connect` (a synchronous
email+password request), Strava's OAuth flow is a two-request redirect
round-trip: the browser is sent to Strava's authorize page, and Strava
later redirects it back to our callback with an authorization code. The
code carries no memory of which client_id/client_secret initiated the
flow, so those are stashed in `app.state.pending_strava_oauth` between
the two requests -- the same "single-admin, server-side-pending-state"
pattern `app.state.pending_garmin_mfa` uses for Garmin's MFA flow (see
app/routes/data_sources.py).
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.dependencies import require_admin_page
from core.providers.strava import StravaAuthError, exchange_code_for_tokens

router = APIRouter()

STRAVA_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
STRAVA_SCOPE = "read,activity:read_all"


@router.post("/oauth/strava/authorize")
def strava_authorize(
    request: Request,
    client_id: str = Form(...),
    client_secret: str = Form(...),
    conn=Depends(require_admin_page),
):
    request.app.state.pending_strava_oauth = {"client_id": client_id, "client_secret": client_secret}
    redirect_uri = str(request.base_url).rstrip("/") + "/oauth/strava/callback"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": STRAVA_SCOPE,
    }
    return RedirectResponse(url=f"{STRAVA_AUTHORIZE_URL}?{urlencode(params)}", status_code=303)


@router.get("/oauth/strava/callback")
def strava_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    conn=Depends(require_admin_page),
):
    pending = request.app.state.pending_strava_oauth
    if error or not code or not pending:
        raise HTTPException(
            status_code=400,
            detail=f"Strava authorization failed or was cancelled: {error or 'missing authorization code'}",
        )

    http_client = request.app.state.strava_http_client_factory()
    try:
        credentials = exchange_code_for_tokens(pending["client_id"], pending["client_secret"], code, http_client)
    except StravaAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        http_client.close()

    request.app.state.strava_credential_store.save(credentials)
    request.app.state.pending_strava_oauth = None
    request.app.state.sync_scheduler.trigger()
    return RedirectResponse(url="/dashboard", status_code=303)
