import asyncio
import threading
import time

from cryptography.fernet import Fernet
from mi_fitness.exceptions import AuthError

from app.mi_fitness_login import PendingMiFitnessLogin, start_mi_fitness_login
from core.security.credentials import CredentialStore


class _FakeAuth:
    """Stands in for XiaomiAuth -- an async context manager whose
    login_qr() fires the qr_callback once (with (qr_image_url, login_url),
    per the real mi_fitness.auth.qr.login_qr signature confirmed by
    introspecting the installed library), then blocks until told the scan
    completed.
    """

    def __init__(self, scan_event, should_fail=False):
        self._scan_event = scan_event
        self._should_fail = should_fail
        self.token = type("Token", (), {"user_id": "user-123"})()
        self.saved_to = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def login_qr(self, *, qr_callback=None, max_wait=None):
        if qr_callback is not None:
            await qr_callback("https://example.com/qr.png", "https://example.com/login")
        # Poll via asyncio.sleep rather than a blocking threading.Event.wait()
        # in an executor thread: the real login_qr's long-poll is built out
        # of ordinary awaits (httpx async calls), so it responds correctly
        # to asyncio.wait_for's cancellation. A blocking wait() handed to
        # run_in_executor does NOT -- once the executor thread has started
        # running it, cancelling the wrapping task cannot interrupt it, so
        # the timeout test would hang forever. This loop keeps the fake
        # faithfully cancellable, matching real behavior.
        while not self._scan_event.is_set():
            await asyncio.sleep(0.01)
        if self._should_fail:
            raise RuntimeError("scan rejected")
        return self.token

    def save_token(self, path):
        self.saved_to = path
        with open(path, "w") as f:
            f.write('{"fake": "token"}')


class _FakeAuthRaisingAuthError:
    """Stands in for XiaomiAuth's internal QR-timeout path: login_qr()
    raises mi_fitness.exceptions.AuthError with the library's real
    untranslated Chinese message, exercising the specific except AuthError
    branch in _run_login (as opposed to _FakeAuth's should_fail=True path,
    which raises a plain RuntimeError and hits the generic except Exception
    branch instead)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def login_qr(self, *, qr_callback=None, max_wait=None):
        if qr_callback is not None:
            await qr_callback("https://example.com/qr.png", "https://example.com/login")
        raise AuthError("二维码扫码超时（300s），请重新获取")


def test_pending_login_starts_in_starting_status():
    pending = PendingMiFitnessLogin()

    assert pending.status == "starting"
    assert pending.qr_image_url is None
    assert pending.error is None


def test_start_mi_fitness_login_reaches_qr_ready_before_scan(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()
    scan_event = threading.Event()

    thread = start_mi_fitness_login(pending, store, auth_factory=lambda: _FakeAuth(scan_event))
    try:
        deadline = time.time() + 2
        while pending.status == "starting" and time.time() < deadline:
            time.sleep(0.02)
        assert pending.status == "qr_ready"
        assert pending.qr_image_url == "https://example.com/qr.png"
    finally:
        scan_event.set()
        thread.join(timeout=2)


def test_start_mi_fitness_login_succeeds_after_scan_and_saves_credentials(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()
    scan_event = threading.Event()

    thread = start_mi_fitness_login(pending, store, auth_factory=lambda: _FakeAuth(scan_event))
    deadline = time.time() + 2
    while pending.status == "starting" and time.time() < deadline:
        time.sleep(0.02)

    scan_event.set()
    thread.join(timeout=2)

    assert pending.status == "success"
    assert store.load() == {"token_file_content": '{"fake": "token"}', "uid": "user-123"}


def test_start_mi_fitness_login_records_error_on_failure(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()
    scan_event = threading.Event()

    thread = start_mi_fitness_login(pending, store, auth_factory=lambda: _FakeAuth(scan_event, should_fail=True))
    deadline = time.time() + 2
    while pending.status == "starting" and time.time() < deadline:
        time.sleep(0.02)

    scan_event.set()
    thread.join(timeout=2)

    assert pending.status == "error"
    assert "scan rejected" in pending.error
    assert store.load() is None  # nothing saved on failure


def test_start_mi_fitness_login_calls_on_success_after_successful_login(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()
    scan_event = threading.Event()
    on_success_calls = []

    thread = start_mi_fitness_login(
        pending, store, auth_factory=lambda: _FakeAuth(scan_event), on_success=lambda: on_success_calls.append(1),
    )
    deadline = time.time() + 2
    while pending.status == "starting" and time.time() < deadline:
        time.sleep(0.02)

    scan_event.set()
    thread.join(timeout=2)

    assert pending.status == "success"
    assert on_success_calls == [1]


def test_start_mi_fitness_login_does_not_call_on_success_after_failure(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()
    scan_event = threading.Event()
    on_success_calls = []

    thread = start_mi_fitness_login(
        pending, store, auth_factory=lambda: _FakeAuth(scan_event, should_fail=True),
        on_success=lambda: on_success_calls.append(1),
    )
    deadline = time.time() + 2
    while pending.status == "starting" and time.time() < deadline:
        time.sleep(0.02)

    scan_event.set()
    thread.join(timeout=2)

    assert pending.status == "error"
    assert on_success_calls == []


def test_start_mi_fitness_login_succeeds_even_if_on_success_raises(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()
    scan_event = threading.Event()

    def failing_on_success():
        raise RuntimeError("scheduler trigger boom")

    thread = start_mi_fitness_login(
        pending, store, auth_factory=lambda: _FakeAuth(scan_event), on_success=failing_on_success,
    )
    deadline = time.time() + 2
    while pending.status == "starting" and time.time() < deadline:
        time.sleep(0.02)

    scan_event.set()
    thread.join(timeout=2)

    assert pending.status == "success"  # on_success failing must not mask the successful login
    assert store.load() is not None


def test_start_mi_fitness_login_translates_library_auth_error_to_english_message(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()

    thread = start_mi_fitness_login(pending, store, auth_factory=lambda: _FakeAuthRaisingAuthError())
    thread.join(timeout=2)

    assert pending.status == "error"
    assert pending.error is not None
    assert "二维码" not in pending.error  # the raw Chinese library message must not reach the user
    assert "please try again" in pending.error.lower()


def test_start_mi_fitness_login_times_out_if_never_scanned(tmp_path):
    store = CredentialStore(Fernet.generate_key(), tmp_path / "creds.enc")
    pending = PendingMiFitnessLogin()
    never_set_event = threading.Event()

    thread = start_mi_fitness_login(
        pending, store, auth_factory=lambda: _FakeAuth(never_set_event), timeout_seconds=0.2,
    )
    thread.join(timeout=2)

    assert pending.status == "error"
    assert "timed out" in pending.error.lower()
