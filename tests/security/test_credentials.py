import pytest
from cryptography.fernet import Fernet, InvalidToken

from core.security.credentials import CredentialStore


def test_save_then_load_roundtrips_credentials(tmp_path):
    key = Fernet.generate_key()
    store = CredentialStore(key, tmp_path / "garmin_credentials.enc")

    store.save({"email": "athlete@example.com", "password": "hunter2"})
    result = store.load()

    assert result == {"email": "athlete@example.com", "password": "hunter2"}


def test_load_returns_none_when_no_credentials_saved(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "garmin_credentials.enc")

    assert store.load() is None


def test_stored_file_is_not_plaintext(tmp_path):
    path = tmp_path / "garmin_credentials.enc"
    store = CredentialStore(Fernet.generate_key(), path)

    store.save({"email": "athlete@example.com", "password": "hunter2"})

    assert b"hunter2" not in path.read_bytes()


def test_wrong_key_cannot_decrypt(tmp_path):
    path = tmp_path / "garmin_credentials.enc"
    CredentialStore(Fernet.generate_key(), path).save({"email": "a@example.com", "password": "x"})

    wrong_store = CredentialStore(Fernet.generate_key(), path)

    with pytest.raises(InvalidToken):
        wrong_store.load()
