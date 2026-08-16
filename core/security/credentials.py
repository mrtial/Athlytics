import json
import os
from pathlib import Path

from cryptography.fernet import Fernet


class CredentialStore:
    def __init__(self, key: bytes, storage_path: Path):
        self._fernet = Fernet(key)
        self._storage_path = storage_path

    def save(self, credentials: dict[str, str]) -> None:
        token = self._fernet.encrypt(json.dumps(credentials).encode("utf-8"))
        self._storage_path.write_bytes(token)
        os.chmod(self._storage_path, 0o600)

    def load(self) -> dict[str, str] | None:
        if not self._storage_path.exists():
            return None
        plaintext = self._fernet.decrypt(self._storage_path.read_bytes())
        return json.loads(plaintext.decode("utf-8"))
