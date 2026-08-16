import os
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values, load_dotenv, set_key

SECRET_KEY_ENV_VAR = "ATHLYTICS_SECRET_KEY"


def get_or_create_secret_key(env_path: Path) -> bytes:
    """Return the ATHLYTICS_SECRET_KEY, generating and persisting one to
    env_path if none exists yet.

    Precedence: an already-set ATHLYTICS_SECRET_KEY in the process
    environment (e.g. injected by Docker/compose) wins over env_path's
    contents, matching python-dotenv's default non-override behavior. If
    such an ambient key is found but env_path doesn't yet contain it, it
    is persisted to env_path so that future reads from this path stay
    consistent regardless of how the current process's environment was
    populated — an ambient key present on one run and absent on the next
    would otherwise cause a fresh key to be generated, permanently
    orphaning any data already encrypted with the ambient key.
    """
    load_dotenv(env_path)
    existing = os.environ.get(SECRET_KEY_ENV_VAR)
    if existing:
        env_path.touch(exist_ok=True)
        os.chmod(env_path, 0o600)
        if dotenv_values(env_path).get(SECRET_KEY_ENV_VAR) != existing:
            set_key(str(env_path), SECRET_KEY_ENV_VAR, existing)
        return existing.encode("utf-8")

    new_key = Fernet.generate_key()
    env_path.touch(exist_ok=True)
    os.chmod(env_path, 0o600)
    set_key(str(env_path), SECRET_KEY_ENV_VAR, new_key.decode("utf-8"))
    os.environ[SECRET_KEY_ENV_VAR] = new_key.decode("utf-8")
    return new_key
