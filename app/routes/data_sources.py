import xml.etree.ElementTree as ET
import zipfile
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.data_sources import SUPPORTED_PROVIDERS, connect_garmin, import_apple_health, ingest_apple_health_metrics
from app.dependencies import require_admin_api, require_admin_page
from app.mi_fitness_login import PendingMiFitnessLogin, start_mi_fitness_login
from core.providers.garmin import GarminAuthError, GarminMfaRequired, complete_garmin_mfa

router = APIRouter()


class AppleHealthMetricsPayload(BaseModel):
    # Attribute can't be named "date" -- it shadows the imported `date` type
    # during Pydantic's annotation resolution ("date | None" -> "NoneType |
    # NoneType"). Alias keeps the wire format ({"date": ...}) unchanged.
    reading_date: date | None = Field(default=None, alias="date")
    readings: dict[str, float]


# NOTE: these two Mi Fitness routes use literal paths, but must stay
# registered ABOVE the parameterized @router.post("/api/data-sources/{provider}/connect")
# route below -- Starlette matches routes in registration order within a
# router, so if the parameterized route came first it would swallow
# "mi-fitness" requests and 422 on the missing email/password Form fields
# it expects (Mi Fitness's connect has no such fields -- it's a QR flow).
@router.post("/api/data-sources/mi-fitness/connect")
def connect_mi_fitness(request: Request, conn=Depends(require_admin_page)):
    pending = PendingMiFitnessLogin()
    request.app.state.pending_mi_fitness_login = pending
    start_mi_fitness_login(
        pending,
        request.app.state.mi_fitness_credential_store,
        on_success=request.app.state.sync_scheduler.trigger,
    )
    return {"status": "started"}


@router.get("/api/data-sources/mi-fitness/status")
def mi_fitness_status(request: Request, conn=Depends(require_admin_page)):
    pending = request.app.state.pending_mi_fitness_login
    if pending is None:
        return {"status": "not_started", "qr_image_url": None, "error": None}
    return {"status": pending.status, "qr_image_url": pending.qr_image_url, "error": pending.error}


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


@router.post("/api/data-sources/apple-health/import")
def import_apple_health_route(
    request: Request,
    export_file: UploadFile = File(...),
    conn=Depends(require_admin_api),
):
    payload = export_file.file.read()
    try:
        result = import_apple_health(conn, payload)
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise HTTPException(status_code=400, detail=f"could not import Apple Health export: {exc}") from exc
    if not result:
        raise HTTPException(
            status_code=400,
            detail="No recognized Apple Health data found in this export.",
        )
    return result


@router.post("/api/data-sources/apple-health/metrics")
def ingest_apple_health_metrics_route(
    payload: AppleHealthMetricsPayload,
    conn=Depends(require_admin_api),
):
    if not payload.readings:
        raise HTTPException(status_code=400, detail="readings must not be empty")
    result = ingest_apple_health_metrics(conn, payload.reading_date or date.today(), payload.readings)
    if not result["imported"]:
        raise HTTPException(
            status_code=400,
            detail=f"No recognized metric types in readings. Unrecognized: {', '.join(result['skipped'])}",
        )
    return result


@router.post("/settings/apple-health/priority")
def set_apple_health_priority(
    request: Request,
    metric_type: str = Form(...),
    preferred_source: str = Form(...),
    conn=Depends(require_admin_page),
):
    from core.storage import repository
    try:
        repository.set_source_priority(conn, metric_type, preferred_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/connections", status_code=303)
