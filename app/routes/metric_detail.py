from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_admin_api
from app.settings import PERSONA_METRIC_TYPES
from app.widgets import build_metric_detail

router = APIRouter()

KNOWN_METRIC_TYPES = set(PERSONA_METRIC_TYPES["full_overview"])
ALLOWED_DAY_RANGES = {7, 30, 90, 365}


@router.get("/api/metric-detail/{metric_type}")
def metric_detail(metric_type: str, days: int = 7, as_of: date | None = None, conn=Depends(require_admin_api)):
    if metric_type not in KNOWN_METRIC_TYPES:
        raise HTTPException(status_code=404, detail="unknown metric_type")
    if days not in ALLOWED_DAY_RANGES:
        raise HTTPException(status_code=400, detail=f"days must be one of {sorted(ALLOWED_DAY_RANGES)}")
    return build_metric_detail(conn, metric_type, days=days, as_of=as_of)
