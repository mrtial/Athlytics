#!/usr/bin/env python3
"""Capture real Tonal API responses as fixtures for parser development.

Run this once, manually, against your own Tonal account:

    python scripts/capture_tonal_fixtures.py --email you@example.com

You will be prompted for your password interactively (never pass it on the
command line -- it would land in shell history).

Writes one pretty-printed JSON file per captured endpoint into
tests/fixtures/tonal/<name>.json. These fixture files are safe to commit --
they are your own workout data on your own self-hosted instance, not a
secret. Your Tonal email/password are NOT written to any fixture file, and
this script never logs them.

See docs/superpowers/specs/2026-08-24-tonal-integration-design.md §9 for
what's still unverified about exact response shapes as of this writing --
after running this script, diff the captured fixtures' keys against that
plan's field-name assumptions before Tasks 3-5 build parsers against them.
"""
import argparse
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from core.providers.tonal_client import TonalClient
from core.security.credentials import CredentialStore

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tonal"


def capture_all(client) -> dict[str, object]:
    """Call each of Tonal's 7 read endpoints once via TonalClient._get(), using
    raw REST paths (the named high-level methods this script would ideally use
    don't exist yet -- they land in a later task). Returns raw responses keyed
    by endpoint name. Uses the first activityId found in the activities
    response for the detail-endpoint call.

    The strength-scores/distribution endpoint's existence is unconfirmed (not
    documented in any reference the integration plan cites), so a failure
    there is caught and recorded as None rather than aborting the whole
    capture run.
    """
    uid = client.user_id

    activities = None
    activities_path_used = None
    for candidate_path in (f"/users/{uid}/activities", f"/users/{uid}/workout-activities"):
        result = client._get(candidate_path, params={"limit": 10})
        print(f"debug: GET {candidate_path}?limit=10 -> {type(result).__name__} with "
              f"{len(result) if hasattr(result, '__len__') else '?'} items", file=sys.stderr)
        if result:
            activities = result
            activities_path_used = candidate_path
            break
    if activities is None:
        activities = result  # keep the last (empty) result so the fixture reflects reality

    # The real API appears to ignore ?limit -- it returned every workout on
    # the account regardless. Fixtures only need a couple of representative
    # samples, not the athlete's full history, so trim client-side before
    # writing to disk (this trimming is fixture-capture-only; it does not
    # reflect how a real TonalClient.get_activities(limit=N) should behave --
    # that's for Task 3 to figure out, possibly with a different param name).
    if activities and len(activities) > 2:
        print(f"debug: server ignored limit param, returned {len(activities)} items; "
              f"trimming fixture to first 2", file=sys.stderr)
        activities = activities[:2]

    if activities:
        print(f"debug: using {activities_path_used} ; first entry keys: {sorted(activities[0].keys())}",
              file=sys.stderr)
        first_activity_id = activities[0].get("activityId") or activities[0].get("id")
        workout_activity_detail = client._get(f"/users/{uid}/workout-activities/{first_activity_id}")
    else:
        print(
            "warning: no workouts found via /activities or /workout-activities; skipping "
            "workout_activity_detail fixture",
            file=sys.stderr,
        )
        workout_activity_detail = None

    try:
        strength_scores_distribution = client._get(f"/users/{uid}/strength-scores/distribution")
    except httpx.HTTPStatusError as exc:
        print(
            f"warning: strength-scores/distribution endpoint returned {exc}; skipping this fixture",
            file=sys.stderr,
        )
        strength_scores_distribution = None

    return {
        "muscle_readiness_current": client._get(f"/users/{uid}/muscle-readiness/current"),
        "strength_scores_current": client._get(f"/users/{uid}/strength-scores/current"),
        "strength_scores_history": client._get(f"/users/{uid}/strength-scores/history"),
        "strength_scores_distribution": strength_scores_distribution,
        "activities": activities,
        "workout_activity_detail": workout_activity_detail,
        "movements": client._get("/movements"),
    }


def write_fixtures(responses: dict[str, object], fixture_dir: Path) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for name, response in responses.items():
        path = fixture_dir / f"{name}.json"
        path.write_text(json.dumps(response, indent=2, default=str))


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--email", default=os.environ.get("TONAL_EMAIL"),
        help="Tonal account email (or set TONAL_EMAIL in .env)",
    )
    args = parser.parse_args()
    if not args.email:
        parser.error("--email is required (or set TONAL_EMAIL in .env)")

    password = os.environ.get("TONAL_PASSWORD") or getpass.getpass(f"Tonal password for {args.email}: ")

    with tempfile.TemporaryDirectory() as tmp_dir:
        credential_store = CredentialStore(
            Fernet.generate_key(), Path(tmp_dir) / "tonal_credentials.enc"
        )
        credential_store.save({"email": args.email, "password": password})

        client = TonalClient(credential_store)
        responses = capture_all(client)

    write_fixtures(responses, FIXTURE_DIR)
    print(f"Wrote {len(responses)} fixture files to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
