from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Get a Google Calendar OAuth refresh token.")
    parser.add_argument("--client-secret-file", type=Path, default=None, help="Path to a local client_secret.json file.")
    args = parser.parse_args()

    load_dotenv()
    secret_file = args.client_secret_file or _env_secret_file()

    if secret_file:
        flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), SCOPES)
    else:
        client_id = os.getenv("GOOGLE_CLIENT_ID") or input("GOOGLE_CLIENT_ID: ").strip()
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET") or input("GOOGLE_CLIENT_SECRET: ").strip()
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    credentials = flow.run_local_server(port=0)
    if not credentials.refresh_token:
        raise RuntimeError("No refresh token returned. Re-consent with prompt=consent if needed.")

    print(credentials.refresh_token)


def _env_secret_file() -> Path | None:
    raw_path = os.getenv("GOOGLE_CLIENT_SECRET_FILE")
    if not raw_path:
        return None
    return Path(raw_path)


if __name__ == "__main__":
    main()
