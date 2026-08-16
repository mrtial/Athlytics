from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.data_sources import SUPPORTED_PROVIDERS, connect_garmin
from app.dependencies import require_admin_page
from core.providers.garmin import GarminAuthError

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
    except GarminAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request.app.state.sync_scheduler.trigger()
    return RedirectResponse(url="/dashboard", status_code=303)
