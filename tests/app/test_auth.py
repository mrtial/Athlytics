from datetime import datetime

import pytest

from app.auth import (
    admin_exists,
    authenticate_admin,
    create_admin,
    create_admin_without_password,
    get_admin,
    hash_password,
    verify_password,
)
from app.db import ensure_app_schema
from core.storage.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    ensure_app_schema(c)
    return c


def test_hash_password_produces_verifiable_hash():
    password_hash, salt = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", password_hash, salt)


def test_verify_password_rejects_wrong_password():
    password_hash, salt = hash_password("correct horse battery staple")

    assert not verify_password("wrong password", password_hash, salt)


def test_hash_password_is_salted_differently_each_call():
    hash_a, salt_a = hash_password("same password")
    hash_b, salt_b = hash_password("same password")

    assert salt_a != salt_b
    assert hash_a != hash_b


def test_admin_exists_false_before_creation(conn):
    assert admin_exists(conn) is False


def test_create_admin_then_admin_exists_and_get_admin_roundtrips(conn):
    create_admin(conn, "athlete", "hunter2hunter2")

    assert admin_exists(conn) is True
    admin = get_admin(conn)
    assert admin.username == "athlete"
    assert verify_password("hunter2hunter2", admin.password_hash, admin.salt)
    assert admin.password_protected is True
    assert isinstance(admin.created_at, datetime)


def test_create_admin_without_password_then_admin_exists_and_is_unprotected(conn):
    create_admin_without_password(conn)

    assert admin_exists(conn) is True
    admin = get_admin(conn)
    assert admin.password_protected is False
    assert isinstance(admin.created_at, datetime)


def test_create_admin_without_password_raises_when_admin_already_exists(conn):
    create_admin_without_password(conn)

    with pytest.raises(ValueError, match="already exists"):
        create_admin_without_password(conn)


def test_authenticate_admin_false_for_passwordless_admin(conn):
    create_admin_without_password(conn)

    assert authenticate_admin(conn, "", "") is False


def test_create_admin_raises_when_admin_already_exists(conn):
    create_admin(conn, "athlete", "hunter2hunter2")

    with pytest.raises(ValueError, match="already exists"):
        create_admin(conn, "someone_else", "another_password")


def test_authenticate_admin_true_for_correct_credentials(conn):
    create_admin(conn, "athlete", "hunter2hunter2")

    assert authenticate_admin(conn, "athlete", "hunter2hunter2") is True


def test_authenticate_admin_false_for_wrong_password(conn):
    create_admin(conn, "athlete", "hunter2hunter2")

    assert authenticate_admin(conn, "athlete", "wrong") is False


def test_authenticate_admin_false_for_wrong_username(conn):
    create_admin(conn, "athlete", "hunter2hunter2")

    assert authenticate_admin(conn, "somebody_else", "hunter2hunter2") is False


def test_authenticate_admin_false_when_no_admin_created(conn):
    assert authenticate_admin(conn, "athlete", "hunter2hunter2") is False
