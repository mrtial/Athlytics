"""Background QR-login orchestration for Mi Fitness (see plan
2026-08-17-mi-fitness-qr-onboarding-flow.md, Task 2).

XiaomiAuth.login_qr() was verified directly against the installed
mi-fitness-python library (not just its README) before writing this
module. Confirmed real signature:

    async def login_qr(
        self,
        *,
        qr_callback: Callable[[str, str], Awaitable[None]] | None = None,
        poll_interval: float = 2.0,
        max_wait: float = 300.0,
    ) -> AuthToken

- It is a single blocking coroutine: internally it fetches the QR code,
  invokes qr_callback once with (qr_image_url, login_url), then long-polls
  until the user scans it (or max_wait elapses) -- there is no separate
  poll-status method to call.
- qr_callback is awaited by the library, so it must be an async def.
- login_qr DOES accept its own timeout (max_wait, default 300s) and raises
  mi_fitness.exceptions.AuthError if the internal long-poll exceeds it.
  However each individual long-poll request can itself take up to 60s
  (poll_request_timeout inside mi_fitness.auth.qr.login_qr), so the
  library's own max_wait can overrun by up to ~60s in the worst case. We
  therefore pass timeout_seconds through as max_wait (so the SDK stops
  polling near the right time) *and* still wrap the call in
  asyncio.wait_for(timeout=timeout_seconds) as a hard outer deadline that
  this module fully controls.
- On success, self.token is mutated in place and returned; auth.token.user_id
  and auth.save_token(path) work immediately afterwards exactly as assumed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable

from mi_fitness.exceptions import AuthError

from core.providers.mi_fitness import save_mi_fitness_session
from core.security.credentials import CredentialStore

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_TIMEOUT_SECONDS = 300.0


@dataclass
class PendingMiFitnessLogin:
    """Server-side state for one in-flight QR login, the same role
    app.state.pending_garmin_mfa / pending_strava_oauth play for their
    flows. Polled by GET /api/data-sources/mi-fitness/status."""

    status: str = "starting"  # "starting" | "qr_ready" | "success" | "error"
    qr_image_url: str | None = None
    error: str | None = None


async def _run_login(
    pending: PendingMiFitnessLogin,
    credential_store: CredentialStore,
    auth_factory: Callable,
    timeout_seconds: float,
    on_success: Callable[[], None] | None = None,
) -> None:
    async def on_qr(qr_image_url: str, login_url: str) -> None:
        pending.qr_image_url = qr_image_url
        pending.status = "qr_ready"

    try:
        auth = auth_factory()
        async with auth:
            await asyncio.wait_for(
                auth.login_qr(qr_callback=on_qr, max_wait=timeout_seconds),
                timeout=timeout_seconds,
            )
            uid = auth.token.user_id
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                token_path = f.name
            try:
                auth.save_token(token_path)
                with open(token_path) as f:
                    token_file_content = f.read()
            finally:
                os.unlink(token_path)
            save_mi_fitness_session(credential_store, token_file_content=token_file_content, uid=uid)
        pending.status = "success"
        if on_success is not None:
            # A failure to kick off the follow-up background sync is not a
            # login failure -- the credentials are already saved and the
            # scheduler will pick this up on its next daily pass regardless.
            # Log it so it isn't silently lost, but never let it flip the
            # already-successful `pending.status` back to "error".
            try:
                on_success()
            except Exception:
                logger.exception("on_success callback failed after Mi Fitness QR login succeeded")
    except asyncio.TimeoutError:
        pending.error = f"QR login timed out after {timeout_seconds:.0f}s -- please try again"
        pending.status = "error"
    except AuthError as exc:
        # The library's own internal timeout (effective_timeout, always <=
        # timeout_seconds) fires before the asyncio.wait_for backstop above
        # in the common case, raising AuthError with an untranslated
        # Chinese message (e.g. "二维码扫码超时（300s），请重新获取"). Log the
        # real diagnostic detail server-side, but show the user a clear
        # English message -- this branch also catches other genuine auth
        # failures, so the wording stays generic rather than assuming every
        # AuthError here is specifically a timeout.
        logger.exception("Mi Fitness QR login raised AuthError: %s", exc)
        pending.error = "Mi Fitness login failed -- please try again"
        pending.status = "error"
    except Exception as exc:
        pending.error = str(exc)
        pending.status = "error"


def start_mi_fitness_login(
    pending: PendingMiFitnessLogin,
    credential_store: CredentialStore,
    auth_factory: Callable = None,
    run_async: Callable = asyncio.run,
    timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
    on_success: Callable[[], None] | None = None,
) -> threading.Thread:
    """Runs the (blocking-until-scanned) QR login coroutine on a background
    thread so the HTTP route that calls this returns immediately. Mirrors
    trigger_full_history_sync's threading.Thread(...).start() pattern in
    app/routes/sync_status.py -- same reason: a slow operation that must
    not hold an HTTP request open.
    """
    if auth_factory is None:
        from mi_fitness import XiaomiAuth

        auth_factory = XiaomiAuth

    thread = threading.Thread(
        target=lambda: run_async(_run_login(pending, credential_store, auth_factory, timeout_seconds, on_success)),
        daemon=True,
    )
    thread.start()
    return thread
