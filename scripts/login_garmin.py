#!/usr/bin/env python3
"""Interactive Garmin login script for completing MFA/2FA challenges.

When your Garmin account has Multi-Factor Authentication (MFA) enabled, the
headless background sync cannot prompt interactively for the SMS/Email code.

Run this script once from your terminal to log in interactively and save the
session tokens to disk:

    python scripts/login_garmin.py

Or specify a custom data directory:

    python scripts/login_garmin.py --data-dir ./data

Once the MFA code is verified, the session tokens are cached in
`<data_dir>/garmin_tokens/` and the headless background sync / web app will
resume automatically.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

from garminconnect import Garmin

# Add project root to sys.path so core modules can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_or_create_secret_key
from core.security.credentials import CredentialStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("ATHLYTICS_DATA_DIR", "./data"),
        help="Path to Athlytics data directory (default: ./data or $ATHLYTICS_DATA_DIR)",
    )
    parser.add_argument("--email", help="Garmin Connect account email (optional; reads from credential store if omitted)")
    parser.add_argument("--token-cache", help="Explicit path for token cache directory (default: <data-dir>/garmin_tokens)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    token_cache_dir = Path(args.token_cache) if args.token_cache else data_dir / "garmin_tokens"
    token_cache_dir.mkdir(parents=True, exist_ok=True)

    email = args.email
    password = None

    # If email not provided, attempt to load saved credentials from data_dir
    credentials_path = data_dir / "garmin_credentials.enc"
    secret_key_path = data_dir / ".env"

    if not email and credentials_path.exists() and secret_key_path.exists():
        try:
            secret_key = get_or_create_secret_key(secret_key_path)
            store = CredentialStore(secret_key, credentials_path)
            creds = store.load()
            if creds:
                email = creds.get("email")
                password = creds.get("password")
                print(f"Loaded saved credentials for: {email}")
        except Exception as e:
            print(f"Note: Could not read stored credentials ({e}); prompting manually.")

    if not email:
        email = input("Garmin Connect email: ").strip()
    if not password:
        password = getpass.getpass(f"Garmin password for {email}: ")

    print("\nConnecting to Garmin Connect SSO...")
    print("If an MFA code is sent to your email or phone, enter it when prompted below.\n")

    def prompt_mfa_callback() -> str:
        print("\n📩 Garmin MFA Challenge Triggered!")
        code = input("Enter the verification code sent to your email/phone: ").strip()
        return code

    try:
        # prompt_mfa provides the interactive input callable for entering the MFA code
        client = Garmin(email, password, prompt_mfa=prompt_mfa_callback)
        client.login(str(token_cache_dir))

        # Save credentials to credential store if not already saved
        if not credentials_path.exists() or not secret_key_path.exists():
            secret_key = get_or_create_secret_key(secret_key_path)
            store = CredentialStore(secret_key, credentials_path)
            store.save({"email": email, "password": password})

        print(f"\n✅ Successfully authenticated with Garmin Connect!")
        print(f"Cached session tokens saved to: {token_cache_dir.resolve()}")
        print("Your background sync and dashboard will now sync headlessly without prompting.")

    except Exception as e:
        print(f"\n❌ Login failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
