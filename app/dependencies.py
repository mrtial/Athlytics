import sqlite3
from typing import Generator

from fastapi import Depends, HTTPException, Request, status

from app.db import ensure_app_schema
from app.session import SESSION_COOKIE_NAME, is_valid_session
from app.settings import get_persona, get_theme
from core.security.credentials import CredentialStore
from core.storage import repository
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


def require_admin_api(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> sqlite3.Connection:
    token = _current_session_token(request)
    if not token or not is_valid_session(conn, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return conn


def onboarding_status(
    conn: sqlite3.Connection, credential_store: CredentialStore, strava_credential_store: CredentialStore | None = None
) -> str:
    """One of "admin"/"persona"/"theme"/"connect"/"complete" -- how far
    onboarding (design doc's Onboarding Flow) has progressed. "connect"'s
    completion is signaled by either Garmin credentials existing or Apple
    Health having synced data. For Garmin, CredentialStore.load() returning
    non-None is the single source of truth for "is connected", and duplicating
    that as a second piece of state risks the two disagreeing (design doc:
    "connect a data source" has no notion of a connection succeeding without
    valid, storable credentials). For Apple Health, synced data is checked via
    repository.has_synced_data().
    """
    from app.auth import admin_exists

    if not admin_exists(conn):
        return "admin"
    if get_persona(conn) is None:
        return "persona"
    if get_theme(conn) is None:
        return "theme"
    garmin_connected = credential_store.load() is not None
    apple_health_connected = repository.has_synced_data(conn, "apple_health")
    strava_connected = strava_credential_store is not None and strava_credential_store.load() is not None
    if not garmin_connected and not apple_health_connected and not strava_connected:
        return "connect"
    return "complete"
