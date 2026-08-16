import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

SESSION_COOKIE_NAME = "athlytics_session"
SESSION_LIFETIME = timedelta(days=30)


def create_session(conn: sqlite3.Connection) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO session (token, created_at, expires_at) VALUES (?, ?, ?)",
        (token, now.isoformat(), (now + SESSION_LIFETIME).isoformat()),
    )
    conn.commit()
    return token


def is_valid_session(conn: sqlite3.Connection, token: str) -> bool:
    row = conn.execute("SELECT expires_at FROM session WHERE token = ?", (token,)).fetchone()
    if row is None:
        return False
    return datetime.fromisoformat(row[0]) > datetime.now(timezone.utc)


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM session WHERE token = ?", (token,))
    conn.commit()
