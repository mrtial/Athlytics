import os
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv, set_key

SECRET_KEY_ENV_VAR = "ATHLYTICS_SECRET_KEY"


def get_or_create_secret_key(env_path: Path) -> bytes:
    load_dotenv(env_path)
    existing = os.environ.get(SECRET_KEY_ENV_VAR)
    if existing:
        return existing.encode("utf-8")

    new_key = Fernet.generate_key()
    env_path.touch(exist_ok=True)
    set_key(str(env_path), SECRET_KEY_ENV_VAR, new_key.decode("utf-8"))
    os.environ[SECRET_KEY_ENV_VAR] = new_key.decode("utf-8")
    return new_key
