from __future__ import annotations

import time
from typing import Any, Callable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import Settings, get_settings
from app.exceptions import EventNotFoundError
from app.models import CalendarEventResult


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
TOKEN_URI = "https://oauth2.googleapis.com/token"
RETRYABLE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


def get_calendar_service(settings: Settings | None = None):
    resolved_settings = settings or get_settings()
    if not resolved_settings.google_client_id or not resolved_settings.google_client_secret:
        raise ValueError("Google OAuth client credentials are not configured.")
    credentials = Credentials(
        token=None,
        refresh_token=resolved_settings.google_refresh_token,
        token_uri=TOKEN_URI,
        client_id=resolved_settings.google_client_id,
        client_secret=resolved_settings.google_client_secret,
        scopes=[CALENDAR_SCOPE],
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


class GoogleCalendarClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.service = get_calendar_service(self.settings)

    def create_event(self, event_body: dict) -> CalendarEventResult:
        response = _execute_with_retries(
            lambda: self.service.events()
            .insert(calendarId=self.settings.google_calendar_id, body=event_body)
            .execute()
        )
        return CalendarEventResult(
            event_id=response["id"],
            html_link=response.get("htmlLink"),
            raw=response,
        )

    def update_event(self, event_id: str, event_body: dict) -> CalendarEventResult:
        try:
            response = _execute_with_retries(
                lambda: self.service.events()
                .patch(
                    calendarId=self.settings.google_calendar_id,
                    eventId=event_id,
                    body=event_body,
                )
                .execute()
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                raise EventNotFoundError(event_id) from exc
            raise

        return CalendarEventResult(
            event_id=response["id"],
            html_link=response.get("htmlLink"),
            raw=response,
        )

    def delete_event(self, event_id: str) -> None:
        try:
            _execute_with_retries(
                lambda: self.service.events()
                .delete(calendarId=self.settings.google_calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                return
            raise


def _execute_with_retries(operation: Callable[[], Any], max_attempts: int = 5) -> Any:
    delay_seconds = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except HttpError as exc:
            if attempt >= max_attempts or not _is_retryable_http_error(exc):
                raise
            time.sleep(delay_seconds)
            delay_seconds *= 2
    raise RuntimeError("Unreachable retry loop exit.")


def _is_retryable_http_error(exc: HttpError) -> bool:
    status = getattr(exc.resp, "status", None)
    if status in {429, 500, 502, 503, 504}:
        return True
    if status != 403:
        return False
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="ignore")
    return any(reason in str(content) for reason in RETRYABLE_REASONS)
