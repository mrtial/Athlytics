# app/settings.py -- minimal stub, replaced by Task 5
import sqlite3


def get_persona(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM app_setting WHERE key = 'persona'").fetchone()
    return row[0] if row else None


def get_theme(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM app_setting WHERE key = 'theme'").fetchone()
    return row[0] if row else None
