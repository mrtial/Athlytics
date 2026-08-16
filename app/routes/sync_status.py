from fastapi import APIRouter, Depends, Request

from app.dependencies import require_admin_api
from app.sync import get_sync_status

router = APIRouter()


@router.get("/api/sync-status")
def sync_status(request: Request, conn=Depends(require_admin_api)):
    status = get_sync_status(conn)
    status["connected"] = request.app.state.credential_store.load() is not None
    return status


@router.post("/api/sync/trigger")
def trigger_sync(request: Request, conn=Depends(require_admin_api)):
    scheduler = getattr(request.app.state, "sync_scheduler", None)
    if scheduler:
        scheduler.trigger()
    return {"status": "triggered"}
