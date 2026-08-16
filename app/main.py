import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import ensure_app_schema
from core.config import get_or_create_secret_key
from core.security.credentials import CredentialStore
from core.storage.db import connect

from app.sync import BackgroundSyncScheduler, perform_sync_pass

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_app(data_dir: Path) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "athlytics.db"
    env_path = data_dir / ".env"
    credentials_path = data_dir / "garmin_credentials.enc"
    token_cache_dir = data_dir / "garmin_tokens"

    conn = connect(db_path)
    ensure_app_schema(conn)
    conn.close()

    secret_key = get_or_create_secret_key(env_path)
    credential_store = CredentialStore(secret_key, credentials_path)

    def sync_fn() -> None:
        perform_sync_pass(
            db_path,
            credential_store,
            token_cache_dir,
            garmin_client_factory=app.state.garmin_client_factory,
        )

    scheduler = BackgroundSyncScheduler(sync_fn)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.sync_scheduler.start()
        yield
        app.state.sync_scheduler.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.data_dir = data_dir
    app.state.db_path = db_path
    app.state.credential_store = credential_store
    app.state.token_cache_dir = token_cache_dir
    app.state.templates = TEMPLATES
    app.state.sync_scheduler = scheduler
    from garminconnect import Garmin

    app.state.garmin_client_factory = Garmin
    app.state.secure_cookies = os.environ.get("ATHLYTICS_SECURE_COOKIES", "false").lower() == "true"

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    from app.routes import auth as auth_routes
    from app.routes import dashboard as dashboard_routes
    from app.routes import data_sources as data_sources_routes
    from app.routes import onboarding as onboarding_routes
    from app.routes import settings as settings_routes
    from app.routes import sync_status as sync_status_routes

    app.include_router(auth_routes.router)
    app.include_router(onboarding_routes.router)
    app.include_router(data_sources_routes.router)
    app.include_router(sync_status_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(settings_routes.router)

    from app.routes import root as root_routes

    app.include_router(root_routes.router)

    return app


def data_dir_from_env() -> Path:
    return Path(os.environ.get("ATHLYTICS_DATA_DIR", "./data"))


def create_production_app() -> FastAPI:
    """Zero-argument factory for `uvicorn --factory app.main:create_production_app`.

    Deliberately not a module-level `app = create_app(...)`: that would run
    directory/DB creation as a side effect of merely *importing* app.main,
    which every test in this plan does (`from app.main import create_app`).
    A bare module-level instantiation would silently create a stray
    `./data` directory in whatever cwd happens to be active when pytest
    (or anything else) imports this module.
    """
    return create_app(data_dir_from_env())
