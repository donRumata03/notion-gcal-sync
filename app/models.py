from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SyncStatus = Literal["created", "updated", "deleted", "unchanged", "skipped", "error"]

DONE_STATUSES = {"done", "completed", "complete", "finished", "closed"}


class NotionDate(BaseModel):
    start: date | datetime
    end: date | datetime | None = None
    time_zone: str | None = None
    is_all_day: bool


class NotionTask(BaseModel):
    page_id: str
    notion_url: str | None = None
    title: str
    date: NotionDate | None = None
    status: str | None = None
    sync_to_calendar: bool | None = None
    duration_minutes: int | None = None
    google_event_id: str | None = None
    sync_hash: str | None = None
    last_sync_error: str | None = None
    calendar_url: str | None = None
    is_archived: bool = False


class CalendarEventResult(BaseModel):
    event_id: str
    html_link: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SyncResult(BaseModel):
    status: SyncStatus
    page_id: str
    event_id: str | None = None
    message: str | None = None
    error: str | None = None


class SyncAllResult(BaseModel):
    total: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[SyncResult] = Field(default_factory=list)


def parse_notion_page(
    page: dict[str, Any],
    *,
    title_property: str = "Name",
    date_property: str = "Date/time",
    status_property: str | None = "Status",
    sync_to_calendar_property: str | None = None,
    duration_minutes_property: str | None = None,
    google_event_id: str | None = None,
    sync_hash: str | None = None,
    last_sync_error: str | None = None,
    calendar_url: str | None = None,
) -> NotionTask:
    duration_value = get_number(page, duration_minutes_property) if duration_minutes_property else None
    duration_minutes = None if duration_value is None else int(duration_value)

    return NotionTask(
        page_id=str(page.get("id", "")),
        notion_url=page.get("url"),
        title=get_title(page, title_property) or "Untitled",
        date=get_date(page, date_property),
        status=get_status(page, status_property) if status_property else None,
        sync_to_calendar=get_checkbox(page, sync_to_calendar_property) if sync_to_calendar_property else None,
        duration_minutes=duration_minutes,
        google_event_id=google_event_id,
        sync_hash=sync_hash,
        last_sync_error=last_sync_error,
        calendar_url=calendar_url,
        is_archived=bool(page.get("archived") or page.get("is_archived") or page.get("in_trash")),
    )


def get_title(page: dict[str, Any], property_name: str = "Name") -> str | None:
    prop = _get_property(page, property_name)
    if not prop:
        return None
    title_items = prop.get("title")
    if not isinstance(title_items, list):
        return None
    return "".join(item.get("plain_text", "") for item in title_items if isinstance(item, dict)) or None


def get_rich_text(page: dict[str, Any], property_name: str) -> str | None:
    prop = _get_property(page, property_name)
    if not prop:
        return None
    rich_text_items = prop.get("rich_text")
    if not isinstance(rich_text_items, list):
        return None
    return "".join(item.get("plain_text", "") for item in rich_text_items if isinstance(item, dict)) or None


def get_status(page: dict[str, Any], property_name: str = "Status") -> str | None:
    prop = _get_property(page, property_name)
    if not prop:
        return None
    status = prop.get("status")
    if not isinstance(status, dict):
        return None
    name = status.get("name")
    return str(name) if name else None


def get_checkbox(page: dict[str, Any], property_name: str = "Sync to Calendar") -> bool | None:
    prop = _get_property(page, property_name)
    if not prop:
        return None
    value = prop.get("checkbox")
    return value if isinstance(value, bool) else None


def get_number(page: dict[str, Any], property_name: str) -> float | int | None:
    prop = _get_property(page, property_name)
    if not prop:
        return None
    value = prop.get("number")
    return value if isinstance(value, (int, float)) else None


def get_date(page: dict[str, Any], property_name: str = "Date/time") -> NotionDate | None:
    prop = _get_property(page, property_name)
    if not prop:
        return None
    date_value = prop.get("date")
    if not isinstance(date_value, dict):
        return None

    start = _parse_temporal(date_value.get("start"))
    if start is None:
        return None

    end = _parse_temporal(date_value.get("end"))
    is_all_day = isinstance(start, date) and not isinstance(start, datetime)
    return NotionDate(
        start=start,
        end=end,
        time_zone=_coerce_string(date_value.get("time_zone")),
        is_all_day=is_all_day,
    )


def is_done_status(status: str | None) -> bool:
    if status is None:
        return False
    return status.strip().lower() in DONE_STATUSES


def _get_property(page: dict[str, Any], property_name: str | None) -> dict[str, Any] | None:
    if not property_name:
        return None
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return None
    prop = properties.get(property_name)
    return prop if isinstance(prop, dict) else None


def _parse_temporal(value: Any) -> date | datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return date.fromisoformat(value)


def _coerce_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
