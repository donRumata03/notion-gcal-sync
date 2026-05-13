from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from typing import Any
import urllib.request
import wsgiref.simple_server

from dotenv import load_dotenv
from google_auth_oauthlib import flow as auth_flow
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Get a Google Calendar OAuth refresh token.")
    parser.add_argument("--client-secret-file", type=Path, default=None, help="Path to a local client_secret.json file.")
    parser.add_argument("--no-open-browser", action="store_true", help="Print the auth URL instead of opening a browser automatically.")
    parser.add_argument("--output-file", type=Path, default=None, help="Optional file path to write the refresh token to.")
    parser.add_argument("--url-file", type=Path, default=None, help="Optional file path to write the consent URL to before waiting for the callback.")
    parser.add_argument("--gcp-project-id", default=None, help="Optional GCP project id for Secret Manager upload and Cloud Run rollout.")
    parser.add_argument("--secret-name", default=None, help="Optional Secret Manager secret name to update with the new token.")
    parser.add_argument("--cloud-run-service", default=None, help="Optional Cloud Run service name to roll after updating the secret.")
    parser.add_argument("--cloud-run-region", default=None, help="Cloud Run region used with --cloud-run-service.")
    parser.add_argument("--gcloud-bin", default="gcloud", help="gcloud executable used for auth and optional service rollout.")
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

    if args.url_file:
        credentials = _run_local_server_with_url_file(
            flow,
            url_file=args.url_file,
            open_browser=not args.no_open_browser,
        )
    else:
        credentials = flow.run_local_server(
            port=0,
            prompt="consent",
            open_browser=not args.no_open_browser,
        )
    if not credentials.refresh_token:
        raise RuntimeError("No refresh token returned. Re-consent with prompt=consent if needed.")

    if args.output_file:
        args.output_file.write_text(credentials.refresh_token, encoding="utf-8")

    if args.secret_name:
        project_id = _require_project_id(args.gcp_project_id)
        version_name = _upload_secret_version(
            project_id=project_id,
            secret_name=args.secret_name,
            secret_value=credentials.refresh_token,
            gcloud_bin=args.gcloud_bin,
        )
        print(f"Uploaded Secret Manager version: {version_name}")

    if args.cloud_run_service:
        project_id = _require_project_id(args.gcp_project_id)
        if not args.cloud_run_region:
            raise ValueError("--cloud-run-region is required with --cloud-run-service.")
        revision = _roll_cloud_run_service(
            project_id=project_id,
            service_name=args.cloud_run_service,
            region=args.cloud_run_region,
            gcloud_bin=args.gcloud_bin,
        )
        print(f"Rolled Cloud Run service to revision: {revision}")

    print(credentials.refresh_token)


def _env_secret_file() -> Path | None:
    raw_path = os.getenv("GOOGLE_CLIENT_SECRET_FILE")
    if not raw_path:
        return None
    return Path(raw_path)


def _require_project_id(project_id: str | None) -> str:
    resolved = project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
    if not resolved:
        raise ValueError("GCP project id is required. Pass --gcp-project-id or set GOOGLE_CLOUD_PROJECT.")
    return resolved


def _run_local_server_with_url_file(
    flow: InstalledAppFlow,
    *,
    url_file: Path,
    open_browser: bool,
) -> object:
    wsgi_app = auth_flow._RedirectWSGIApp("The authentication flow has completed. You may close this window.")
    wsgiref.simple_server.WSGIServer.allow_reuse_address = False
    local_server = wsgiref.simple_server.make_server(
        "localhost",
        0,
        wsgi_app,
        handler_class=auth_flow._WSGIRequestHandler,
    )

    try:
        flow.redirect_uri = f"http://localhost:{local_server.server_port}/"
        auth_url, _ = flow.authorization_url(prompt="consent")
        url_file.write_text(auth_url, encoding="utf-8")
        print(f"Consent URL written to {url_file}")

        if open_browser:
            import webbrowser

            webbrowser.open(auth_url, new=1, autoraise=True)

        local_server.handle_request()
        try:
            authorization_response = wsgi_app.last_request_uri.replace("http", "https")
        except AttributeError as exc:
            raise auth_flow.WSGITimeoutError("Timed out waiting for response from authorization server") from exc

        flow.fetch_token(authorization_response=authorization_response)
    finally:
        local_server.server_close()

    return flow.credentials


def _upload_secret_version(
    *,
    project_id: str,
    secret_name: str,
    secret_value: str,
    gcloud_bin: str,
) -> str:
    access_token = _get_gcloud_access_token(gcloud_bin)
    payload = {
        "payload": {
            "data": base64.b64encode(secret_value.encode("utf-8")).decode("ascii"),
        }
    }
    response = _post_json(
        url=f"https://secretmanager.googleapis.com/v1/projects/{project_id}/secrets/{secret_name}:addVersion",
        body=payload,
        access_token=access_token,
    )
    version_name = response.get("name")
    if not isinstance(version_name, str) or not version_name:
        raise RuntimeError(f"Secret Manager response did not include a version name: {response}")
    return version_name


def _roll_cloud_run_service(
    *,
    project_id: str,
    service_name: str,
    region: str,
    gcloud_bin: str,
) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    subprocess.run(
        [
            gcloud_bin,
            "run",
            "services",
            "update",
            service_name,
            "--project",
            project_id,
            "--region",
            region,
            "--update-env-vars",
            f"CONFIG_REFRESH={stamp}",
        ],
        check=True,
    )
    result = subprocess.run(
        [
            gcloud_bin,
            "run",
            "services",
            "describe",
            service_name,
            "--project",
            project_id,
            "--region",
            region,
            "--format=value(status.latestReadyRevisionName)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _get_gcloud_access_token(gcloud_bin: str) -> str:
    result = subprocess.run(
        [gcloud_bin, "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _post_json(*, url: str, body: dict[str, Any], access_token: str) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


if __name__ == "__main__":
    main()
