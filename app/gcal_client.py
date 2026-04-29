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
    def __init__(self, settings: Settings | None = None, calendar_id: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.calendar_id = calendar_id or self.settings.google_calendar_id
        self.service = get_calendar_service(self.settings)

    def create_event(self, event_body: dict) -> CalendarEventResult:
        response = _execute_with_retries(
            lambda: self.service.events()
            .insert(calendarId=self.calendar_id, body=event_body)
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
                    calendarId=self.calendar_id,
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
                .delete(calendarId=self.calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                return
            raise

    def find_events_by_notion_page_id(self, page_id: str) -> list[CalendarEventResult]:
        events_by_id: dict[str, CalendarEventResult] = {}

        for event in self._list_events(privateExtendedProperty=f"notionPageId={page_id}"):
            _add_event_result(events_by_id, event)

        normalized_page_id = page_id.replace("-", "")
        if normalized_page_id != page_id:
            for event in self._list_events(privateExtendedProperty=f"notionPageId={normalized_page_id}"):
                _add_event_result(events_by_id, event)

        for event in self._list_events(q=page_id):
            if f"Page ID: {page_id}" in str(event.get("description", "")):
                _add_event_result(events_by_id, event)

        return sorted(events_by_id.values(), key=_event_sort_key)

    def _list_events(self, **params: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            request_params = {
                "calendarId": self.calendar_id,
                "showDeleted": False,
                "singleEvents": True,
                "maxResults": 250,
                **params,
            }
            if page_token:
                request_params["pageToken"] = page_token

            response = _execute_with_retries(lambda: self.service.events().list(**request_params).execute())
            events.extend(item for item in response.get("items", []) if item.get("id"))
            page_token = response.get("nextPageToken")
            if not page_token:
                return events


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


def _add_event_result(events_by_id: dict[str, CalendarEventResult], event: dict[str, Any]) -> None:
    event_id = event.get("id")
    if not isinstance(event_id, str):
        return
    events_by_id[event_id] = CalendarEventResult(
        event_id=event_id,
        html_link=event.get("htmlLink"),
        raw=event,
    )


def _event_sort_key(event: CalendarEventResult) -> tuple[str, str]:
    raw_start = event.raw.get("start")
    if isinstance(raw_start, dict):
        start = str(raw_start.get("dateTime") or raw_start.get("date") or "")
    else:
        start = ""
    return (start, event.event_id)
