from fastapi import APIRouter, Depends, Request

from app.dependencies import require_admin_page
from app.qr import apple_health_shortcut_qr_svg
from app.settings import (
    DEFAULT_SKIN,
    DEFAULT_THEME,
    generate_api_token,
    get_api_token,
    get_athlete_age,
    get_athlete_name,
    get_persona,
    get_skin,
    get_theme,
    set_api_token,
)
from app.sync import get_sync_status
from core.providers.apple_health import APPLE_HEALTH_METRIC_TYPES
from core.providers.garmin import GARMIN_METRIC_TYPES
from core.providers.registry import PROVIDER_REGISTRY
from core.storage.repository import get_source_priority

router = APIRouter()


@router.get("/connections")
def connections_page(request: Request, conn=Depends(require_admin_page)):
    templates = request.app.state.templates
    theme = get_theme(conn) or DEFAULT_THEME
    skin = get_skin(conn) or DEFAULT_SKIN
    athlete_name = get_athlete_name(conn)
    athlete_age = get_athlete_age(conn)
    persona = get_persona(conn)

    connected_by_id = {p.id: p.is_connected(conn, request.app.state) for p in PROVIDER_REGISTRY}
    providers = sorted(
        (
            {
                "id": p.id,
                "display_name": p.display_name,
                "flow_type": p.flow_type,
                "connected": connected_by_id[p.id],
            }
            for p in PROVIDER_REGISTRY
        ),
        key=lambda p: not p["connected"],
    )

    # Apple Health is a one-shot import, not an ongoing sync run (no
    # sync_run_status/sync_metric_status rows to show) -- its own "Status:
    # connected" line in the panel already covers it.
    sync_status_by_id = {}
    for p in PROVIDER_REGISTRY:
        if connected_by_id[p.id] and p.flow_type != "file_import":
            sync_status_by_id[p.id] = get_sync_status(conn, p.id)

    overlapping_metric_types = []
    if connected_by_id.get("garmin") and connected_by_id.get("apple_health"):
        overlapping_metric_types = sorted(set(GARMIN_METRIC_TYPES) & set(APPLE_HEALTH_METRIC_TYPES))

    sync_in_progress = request.app.state.sync_scheduler.is_syncing()
    currently_syncing_source = request.app.state.currently_syncing_source
    sync_metric_progress = request.app.state.sync_metric_progress

    api_token = get_api_token(conn)
    if api_token is None:
        api_token = generate_api_token()
        set_api_token(conn, api_token)
    apple_health_upload_url = str(request.base_url).rstrip("/") + "/api/data-sources/apple-health/import"
    apple_health_shortcut_qr = apple_health_shortcut_qr_svg(api_token, apple_health_upload_url)
    apple_health_metrics_url = str(request.base_url).rstrip("/") + "/api/data-sources/apple-health/metrics"
    apple_health_metrics_qr = apple_health_shortcut_qr_svg(api_token, apple_health_metrics_url)

    return templates.TemplateResponse(
        request=request,
        name="connections.html",
        context={
            "authenticated": True,
            "active_page": "connections",
            "theme": theme,
            "skin": skin,
            "athlete_name": athlete_name,
            "athlete_age": athlete_age,
            "persona": persona,
            "providers": providers,
            "sync_status_by_id": sync_status_by_id,
            "sync_in_progress": sync_in_progress,
            "currently_syncing_source": currently_syncing_source,
            "sync_metric_progress": sync_metric_progress,
            "id_prefix": "connections",
            "api_token": api_token,
            "apple_health_upload_url": apple_health_upload_url,
            "apple_health_shortcut_qr": apple_health_shortcut_qr,
            "apple_health_metrics_url": apple_health_metrics_url,
            "apple_health_metrics_qr": apple_health_metrics_qr,
            "apple_health_metric_types": sorted(APPLE_HEALTH_METRIC_TYPES),
            "apple_health_success_redirect": None,
            "strava_import_success_redirect": None,
            "mi_fitness_success_url": None,
            "overlapping_metric_types": overlapping_metric_types,
            "source_priority": {mt: get_source_priority(conn, mt) or "garmin" for mt in overlapping_metric_types},
            "show_regenerate_token": True,
            "show_full_history_sync": True,
        },
    )
