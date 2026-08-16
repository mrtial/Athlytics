from dotenv import dotenv_values
from cryptography.fernet import Fernet

from core.config import SECRET_KEY_ENV_VAR, get_or_create_secret_key


def test_creates_and_persists_secret_key_on_first_call(tmp_path, monkeypatch):
    monkeypatch.delenv(SECRET_KEY_ENV_VAR, raising=False)
    env_path = tmp_path / ".env"

    key = get_or_create_secret_key(env_path)

    assert Fernet(key)  # raises if not a well-formed Fernet key
    assert dotenv_values(env_path)[SECRET_KEY_ENV_VAR] == key.decode()


def test_reuses_existing_secret_key_on_subsequent_calls(tmp_path, monkeypatch):
    monkeypatch.delenv(SECRET_KEY_ENV_VAR, raising=False)
    env_path = tmp_path / ".env"

    first_key = get_or_create_secret_key(env_path)
    monkeypatch.delenv(SECRET_KEY_ENV_VAR, raising=False)
    second_key = get_or_create_secret_key(env_path)

    assert first_key == second_key


def test_generated_env_file_has_restrictive_permissions(tmp_path, monkeypatch):
    monkeypatch.delenv(SECRET_KEY_ENV_VAR, raising=False)
    env_path = tmp_path / ".env"

    get_or_create_secret_key(env_path)

    assert oct(env_path.stat().st_mode)[-3:] == "600"


def test_ambient_secret_key_is_persisted_to_env_path(tmp_path, monkeypatch):
    ambient_key = Fernet.generate_key().decode()
    monkeypatch.setenv(SECRET_KEY_ENV_VAR, ambient_key)
    env_path = tmp_path / ".env"

    result = get_or_create_secret_key(env_path)

    assert result == ambient_key.encode()
    assert dotenv_values(env_path)[SECRET_KEY_ENV_VAR] == ambient_key
