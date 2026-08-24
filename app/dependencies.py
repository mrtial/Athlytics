import hmac
import sqlite3
from typing import Generator

from fastapi import Depends, HTTPException, Request, status

from app.db import ensure_app_schema
from app.session import SESSION_COOKIE_NAME, is_valid_session
from app.settings import get_api_token, get_persona, get_theme
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


def onboarding_status(conn: sqlite3.Connection, state: object) -> str:
    """One of "admin"/"persona"/"theme"/"connect"/"complete" -- how far
    onboarding has progressed. "connect" completes as soon as any one
    PROVIDER_REGISTRY entry's is_connected(conn, state) is true.
    """
    from app.auth import admin_exists
    from core.providers.registry import PROVIDER_REGISTRY

    if not admin_exists(conn):
        return "admin"
    if get_persona(conn) is None:
        return "persona"
    if get_theme(conn) is None:
        return "theme"
    if not any(p.is_connected(conn, state) for p in PROVIDER_REGISTRY):
        return "connect"
    return "complete"
