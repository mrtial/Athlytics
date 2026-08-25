import hmac
import sqlite3
from typing import Generator

from fastapi import Depends, HTTPException, Request, status

from app.db import ensure_app_schema
from app.session import SESSION_COOKIE_NAME, is_valid_session
from app.settings import get_api_token, get_persona, get_setting, get_theme
from core.storage.db import connect


def get_conn(request: Request) -> Generator[sqlite3.Connection, None, None]:
    conn = connect(request.app.state.db_path)
    ensure_app_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _current_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def require_admin_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> sqlite3.Connection:
    token = _current_session_token(request)
    if not token or not is_valid_session(conn, token):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return conn


def _has_valid_bearer_token(request: Request, conn: sqlite3.Connection) -> bool:
    """Non-cookie auth for programmatic clients (e.g. an iOS Shortcut) that
    can't carry a browser session cookie. Compared against the single
    admin's API token (app.settings.get_api_token) with hmac.compare_digest
    to avoid a timing side-channel."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    presented = auth_header.removeprefix("Bearer ")
    api_token = get_api_token(conn)
    return api_token is not None and hmac.compare_digest(presented, api_token)


def require_admin_api(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> sqlite3.Connection:
    token = _current_session_token(request)
    if token and is_valid_session(conn, token):
        return conn
    if _has_valid_bearer_token(request, conn):
        return conn
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")


def is_password_protected(conn: sqlite3.Connection) -> bool:
    """Whether the single admin has a login password set. An athlete who
    skipped that step during onboarding shouldn't be shown a "Log out"
    option -- /login's passwordless re-issue (routes/auth.py) would just
    log them straight back in, making it a dead end rather than a real
    logout.
    """
    from app.auth import get_admin

    admin = get_admin(conn)
    return admin.password_protected if admin else True


def onboarding_status(conn: sqlite3.Connection, state: object) -> str:
    """One of "admin"/"profile"/"persona"/"theme"/"connect"/"complete" --
    how far onboarding has progressed. "connect" completes as soon as any
    one PROVIDER_REGISTRY entry's is_connected(conn, state) is true.
    """
    from app.auth import admin_exists
    from core.providers.registry import PROVIDER_REGISTRY

    if not admin_exists(conn):
        return "admin"
    if get_setting(conn, "athlete_name") is None:
        return "profile"
    if get_persona(conn) is None:
        return "persona"
    if get_theme(conn) is None:
        return "theme"
    if not any(p.is_connected(conn, state) for p in PROVIDER_REGISTRY):
        return "connect"
    return "complete"


ONBOARDING_STEPS = [
    ("admin", "Account", "/onboarding/admin"),
    ("profile", "Profile", "/onboarding/profile"),
    ("persona", "Persona", "/onboarding/persona"),
    ("theme", "Theme", "/onboarding/theme"),
    ("connect", "Connect", "/onboarding/connect"),
]
_ONBOARDING_STEP_ORDER = [step_id for step_id, _, _ in ONBOARDING_STEPS]


def onboarding_progress(conn: sqlite3.Connection, state: object, current: str) -> list[dict]:
    """The step tracker shown at the top of every onboarding page: each
    step the athlete has already completed (or is currently on) becomes a
    link back to revisit and change it; steps not reached yet stay inert --
    nothing here enforces order server-side (each step route only ever
    required require_admin_page, same as before this tracker existed), this
    just decides what's worth surfacing as a clickable link.
    """
    status = onboarding_status(conn, state)
    current_index = _ONBOARDING_STEP_ORDER.index(status) if status in _ONBOARDING_STEP_ORDER else len(_ONBOARDING_STEP_ORDER)
    return [
        {
            "id": step_id,
            "label": label,
            "url": url,
            "is_current": step_id == current,
            "is_reachable": _ONBOARDING_STEP_ORDER.index(step_id) <= current_index,
        }
        for step_id, label, url in ONBOARDING_STEPS
    ]
