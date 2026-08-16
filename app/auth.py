import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

PBKDF2_ITERATIONS = 600_000


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Returns (password_hash_hex, salt_hex). Generates a fresh random salt
    if none is given (the normal case: creating a new hash). An explicit
    salt is only passed back in by verify_password(), to recompute the hash
    for comparison against a stored one.
    """
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate_hash, _ = hash_password(password, bytes.fromhex(salt))
    return hmac.compare_digest(candidate_hash, password_hash)


@dataclass(frozen=True)
class AdminUser:
    username: str
    password_hash: str
    salt: str
    created_at: datetime


def admin_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM admin_user WHERE id = 1").fetchone() is not None


def create_admin(conn: sqlite3.Connection, username: str, password: str) -> None:
    """Creates the single admin account. Raises ValueError if one already
    exists -- Athlytics is single-tenant, single-admin (design doc Users
    section: "one admin login"), so this must never silently overwrite an
    existing admin.
    """
    if admin_exists(conn):
        raise ValueError("admin user already exists")
    password_hash, salt = hash_password(password)
    conn.execute(
        "INSERT INTO admin_user (id, username, password_hash, salt, created_at) VALUES (1, ?, ?, ?, ?)",
        (username, password_hash, salt, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_admin(conn: sqlite3.Connection) -> AdminUser | None:
    row = conn.execute(
        "SELECT username, password_hash, salt, created_at FROM admin_user WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return AdminUser(username=row[0], password_hash=row[1], salt=row[2], created_at=datetime.fromisoformat(row[3]))


def authenticate_admin(conn: sqlite3.Connection, username: str, password: str) -> bool:
    admin = get_admin(conn)
    if admin is None or admin.username != username:
        return False
    return verify_password(password, admin.password_hash, admin.salt)
