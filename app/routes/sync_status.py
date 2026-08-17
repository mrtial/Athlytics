import threading

from fastapi import APIRouter, Depends, Request

from app.dependencies import require_admin_api
from app.sync import get_sync_status, perform_sync_pass

router = APIRouter()


@router.get("/api/sync-status")
def sync_status(request: Request, conn=Depends(require_admin_api)):
    status = get_sync_status(conn, "garmin")
    status["connected"] = request.app.state.credential_store.load() is not None

    if request.app.state.strava_credential_store.load() is not None:
        strava_status = get_sync_status(conn, "strava")
        strava_status["connected"] = True
        status["strava"] = strava_status

    return status


@router.post("/api/sync/trigger")
def trigger_sync(request: Request, conn=Depends(require_admin_api)):
    scheduler = getattr(request.app.state, "sync_scheduler", None)
    if scheduler:
        scheduler.trigger()
    return {"status": "triggered"}


@router.post("/api/sync/full-history")
def trigger_full_history_sync(request: Request, conn=Depends(require_admin_api)):
    """One-off, manually-triggered resync of a metric's entire history,
    ignoring checkpoints -- distinct from the always-incremental background
    scheduler (see perform_sync_pass's force_full_backfill). Runs in a
    background thread since a full backfill can take minutes.
    """
    state = request.app.state
    threading.Thread(
        target=perform_sync_pass,
        args=(state.db_path, state.credential_store, state.token_cache_dir),
        kwargs={"garmin_client_factory": state.garmin_client_factory, "force_full_backfill": True},
        daemon=True,
    ).start()
    return {"status": "started"}
