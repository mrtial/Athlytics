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

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class _NullScheduler:
    """Placeholder used until Task 6 replaces app.state.sync_scheduler with
    the real BackgroundSyncScheduler. Keeps create_app() fully functional
    (and its state shape stable) before Task 6 exists."""

    def start(self) -> None:
        pass

    def trigger(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> None:
        pass


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
    scheduler = _NullScheduler()

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

    @app.get("/")
    def root_placeholder():
        # Replaced by the real onboarding-aware dispatcher in Task 11.
        return RedirectResponse(url="/dashboard", status_code=303)

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
