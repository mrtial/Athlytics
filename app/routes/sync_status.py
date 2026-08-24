from fastapi import APIRouter, Depends, Request

from app.dependencies import require_admin_api
from app.sync import get_sync_status
from core.providers.registry import PROVIDER_REGISTRY

router = APIRouter()


@router.get("/api/sync-status")
def sync_status(request: Request, conn=Depends(require_admin_api)):
    providers: dict[str, dict] = {}
    for provider in PROVIDER_REGISTRY:
        connected = provider.is_connected(conn, request.app.state)
        if provider.flow_type == "file_import":
            # Apple Health has no ongoing sync run -- only a one-shot import.
            providers[provider.id] = {"connected": connected, "last_run_at": None, "auth_error": None, "metrics": []}
        else:
            status = get_sync_status(conn, provider.id)
            status["connected"] = connected
            providers[provider.id] = status

    scheduler = getattr(request.app.state, "sync_scheduler", None)
    return {
        "providers": providers,
        "sync_in_progress": scheduler.is_syncing() if scheduler else False,
        "currently_syncing_source": getattr(request.app.state, "currently_syncing_source", None),
        "sync_metric_progress": getattr(request.app.state, "sync_metric_progress", None),
    }


@router.post("/api/sync/trigger")
def trigger_sync(request: Request, conn=Depends(require_admin_api)):
    scheduler = getattr(request.app.state, "sync_scheduler", None)
    if scheduler:
        scheduler.trigger()
    return {"status": "triggered"}


@router.post("/api/sync/full-history")
def trigger_full_history_sync(request: Request, conn=Depends(require_admin_api)):
    """One-off, manually-triggered resync of every connected source's entire
    history, ignoring checkpoints -- routed through the scheduler's single
    background thread (the same one the routine incremental sync uses)
    rather than a separate ad-hoc thread, so the two can never run
    concurrently and race the same provider APIs. As a result this also
    covers every connected source, not just Garmin -- it's the same sync_fn
    the regular pass already uses.
    """
    scheduler = getattr(request.app.state, "sync_scheduler", None)
    if scheduler:
        scheduler.trigger(force_full_backfill=True)
    return {"status": "triggered"}
