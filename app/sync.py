from __future__ import annotations

from time import sleep
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

from app.config import Settings, SyncMapping, get_settings
from app.exceptions import EventNotFoundError
from app.hashing import compute_sync_hash
from app.logging_utils import get_logger
from app.models import CalendarEventResult, NotionTask, SyncAllResult, SyncResult, parse_notion_page
from app.state_store import SyncStateStore


logger = get_logger(__name__)


class NotionClientProtocol(Protocol):
    def get_page(self, page_id: str) -> dict[str, Any]: ...

    def query_database_for_sync_candidates(self) -> list[dict[str, Any]]: ...


class GoogleCalendarClientProtocol(Protocol):
    def create_event(self, event_body: dict[str, Any]) -> CalendarEventResult: ...

    def update_event(self, event_id: str, event_body: dict[str, Any]) -> CalendarEventResult: ...

    def delete_event(self, event_id: str) -> None: ...


def decide_sync_action(task: NotionTask, settings: Settings) -> str:
    del settings

    has_event = bool(task.google_event_id)
    if task.is_archived or task.date is None:
        return "delete_event" if has_event else "skip_without_event"
    return "upsert_event"


def build_calendar_event(task: NotionTask, settings: Settings) -> dict[str, Any]:
    if task.date is None:
        raise ValueError("Task date is required to build a calendar event.")

    event_body = {
        "summary": task.title,
        "description": _build_description(task),
        "source": {"title": "Notion", "url": task.notion_url} if task.notion_url else {"title": "Notion"},
        "extendedProperties": {"private": {"notionPageId": task.page_id}},
    }
    event_body.update(_build_event_dates(task, settings))
    return event_body


def sync_page_object(
    page: dict[str, Any],
    notion_client: NotionClientProtocol,
    gcal_client: GoogleCalendarClientProtocol,
    state_store: SyncStateStore,
    settings: Settings | None = None,
    mapping: SyncMapping | None = None,
) -> SyncResult:
    resolved_settings = settings or get_settings()
    resolved_mapping = mapping or resolved_settings.resolved_sync_mappings()[0]
    mapping_id = resolved_mapping.id or "default"
    state = state_store.get_record(page["id"], mapping_id=mapping_id)
    task = parse_notion_page(
        page,
        title_property=resolved_mapping.title_property,
        date_property=resolved_mapping.date_property,
        status_property=resolved_mapping.status_property,
        sync_to_calendar_property=resolved_mapping.sync_to_calendar_property,
        duration_minutes_property=resolved_mapping.duration_minutes_property,
        google_event_id=state.event_id if state else None,
        sync_hash=state.sync_hash if state else None,
        last_sync_error=state.last_error if state else None,
        calendar_url=state.calendar_url if state else None,
    )

    try:
        action = decide_sync_action(task, resolved_settings)
        if action == "upsert_event" and not page_matches_mapping_filters(page, resolved_mapping):
            action = "delete_event" if task.google_event_id else "skip_without_event"

        if action == "skip_without_event":
            return SyncResult(status="skipped", page_id=task.page_id, event_id=task.google_event_id, message="No sync action required.")

        if action == "delete_event":
            if not task.google_event_id:
                return SyncResult(status="skipped", page_id=task.page_id, message="No calendar event to delete.")
            gcal_client.delete_event(task.google_event_id)
            state_store.delete_record(task.page_id, mapping_id=mapping_id)
            return SyncResult(status="deleted", page_id=task.page_id, event_id=task.google_event_id, message="Calendar event deleted.")

        event_body = build_calendar_event(task, resolved_settings)
        new_hash = compute_sync_hash(event_body)

        if task.google_event_id and task.sync_hash == new_hash:
            return SyncResult(status="unchanged", page_id=task.page_id, event_id=task.google_event_id, message="Calendar event already up to date.")

        if task.google_event_id:
            try:
                result = gcal_client.update_event(task.google_event_id, event_body)
                _sleep_after_calendar_write(resolved_settings)
                state_store.upsert_success(task.page_id, result.event_id, new_hash, result.html_link, mapping_id=mapping_id)
                return SyncResult(status="updated", page_id=task.page_id, event_id=result.event_id, message="Calendar event updated.")
            except EventNotFoundError:
                pass

        result = gcal_client.create_event(event_body)
        _sleep_after_calendar_write(resolved_settings)
        state_store.upsert_success(task.page_id, result.event_id, new_hash, result.html_link, mapping_id=mapping_id)
        return SyncResult(status="created", page_id=task.page_id, event_id=result.event_id, message="Calendar event created.")
    except Exception as exc:
        logger.exception("Page sync failed.", extra={"page_id": task.page_id})
        try:
            state_store.upsert_error(task.page_id, str(exc), mapping_id=mapping_id)
        except Exception:
            logger.exception("Failed to write sync error to local state store.", extra={"page_id": task.page_id})
        return SyncResult(status="error", page_id=task.page_id, event_id=task.google_event_id, error=str(exc))


def sync_page(
    page_id: str,
    notion_client: NotionClientProtocol | None = None,
    gcal_client: GoogleCalendarClientProtocol | None = None,
    state_store: SyncStateStore | None = None,
    settings: Settings | None = None,
) -> SyncAllResult:
    resolved_settings = settings or get_settings()
    resolved_state_store = state_store or SyncStateStore(resolved_settings)

    try:
        page = (notion_client or _make_default_notion_client(resolved_settings)).get_page(page_id)
    except Exception as exc:
        logger.exception("Failed to retrieve Notion page.", extra={"page_id": page_id})
        result = SyncAllResult(total=1, errors=1)
        result.results.append(SyncResult(status="error", page_id=page_id, error=str(exc)))
        return result

    return sync_page_payload(page, notion_client, gcal_client, resolved_state_store, resolved_settings)


def sync_page_payload(
    page: dict[str, Any],
    notion_client: NotionClientProtocol | None = None,
    gcal_client: GoogleCalendarClientProtocol | None = None,
    state_store: SyncStateStore | None = None,
    settings: Settings | None = None,
) -> SyncAllResult:
    resolved_settings = settings or get_settings()
    resolved_state_store = state_store or SyncStateStore(resolved_settings)
    result = SyncAllResult()
    mappings = mappings_for_page(page, resolved_settings)
    result.total = len(mappings)

    if not mappings:
        result.skipped = 1
        result.results.append(SyncResult(status="skipped", page_id=page.get("id", "unknown"), message="No mapping matches page parent."))
        return result

    for mapping in mappings:
        resolved_notion_client, resolved_gcal_client, _ = _resolve_clients(
            notion_client, gcal_client, resolved_state_store, resolved_settings, mapping
        )
        sync_result = sync_page_object(
            page, resolved_notion_client, resolved_gcal_client, resolved_state_store, resolved_settings, mapping
        )
        _add_sync_result(result, sync_result)
    return result


def sync_all(
    notion_client: NotionClientProtocol | None = None,
    gcal_client: GoogleCalendarClientProtocol | None = None,
    state_store: SyncStateStore | None = None,
    settings: Settings | None = None,
) -> SyncAllResult:
    resolved_settings = settings or get_settings()
    result = SyncAllResult()
    resolved_state_store = state_store or SyncStateStore(resolved_settings)

    for mapping in resolved_settings.resolved_sync_mappings():
        resolved_notion_client, resolved_gcal_client, _ = _resolve_clients(
            notion_client, gcal_client, resolved_state_store, resolved_settings, mapping
        )
        live_pages = resolved_notion_client.query_database_for_sync_candidates()
        pages_by_id = {page["id"]: page for page in live_pages}
        mapping_id = mapping.id or "default"
        for tracked_page_id in resolved_state_store.list_page_ids(mapping_id=mapping_id):
            if tracked_page_id not in pages_by_id:
                pages_by_id[tracked_page_id] = resolved_notion_client.get_page(tracked_page_id)

        pages = list(pages_by_id.values())
        result.total += len(pages)

        for page in pages:
            try:
                sync_result = sync_page_object(
                    page, resolved_notion_client, resolved_gcal_client, resolved_state_store, resolved_settings, mapping
                )
            except Exception as exc:
                page_id = page.get("id", "unknown")
                logger.exception("Unhandled exception during page sync.", extra={"page_id": page_id})
                sync_result = SyncResult(status="error", page_id=page_id, error=str(exc))
            _add_sync_result(result, sync_result)

    return result


def extract_page_ids_from_webhook(payload: dict[str, Any]) -> set[str]:
    page_ids: set[str] = set()

    entity = payload.get("entity")
    if isinstance(entity, dict) and _is_page_object(entity) and isinstance(entity.get("id"), str):
        page_ids.add(entity["id"])

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if _is_page_object(value) and isinstance(value.get("id"), str):
                page_ids.add(value["id"])
            for key, child in value.items():
                if key == "page_id" and isinstance(child, str):
                    page_ids.add(child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return page_ids


def extract_page_payloads_from_webhook(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pages: dict[str, dict[str, Any]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if _looks_like_full_page_payload(value) and isinstance(value.get("id"), str):
                pages[value["id"]] = value
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return pages


def _build_description(task: NotionTask) -> str:
    lines = [line for line in [task.notion_url, f"Page ID: {task.page_id}", "Synced from Notion"] if line]
    return "\n".join(lines)


def _build_event_dates(task: NotionTask, settings: Settings) -> dict[str, Any]:
    notion_date = task.date
    if notion_date is None:
        raise ValueError("Task date is required to build calendar dates.")
    if notion_date.is_all_day:
        start_date = notion_date.start
        if not isinstance(start_date, date) or isinstance(start_date, datetime):
            raise ValueError("All-day Notion dates must use date values.")
        if notion_date.end is None:
            end_date = start_date + timedelta(days=1)
        else:
            raw_end = notion_date.end
            if not isinstance(raw_end, date) or isinstance(raw_end, datetime):
                raise ValueError("All-day Notion date range end must use date values.")
            end_date = raw_end + timedelta(days=1)
        return {
            "start": {"date": start_date.isoformat()},
            "end": {"date": end_date.isoformat()},
        }

    start_dt = _coerce_datetime(notion_date.start)
    if notion_date.end is None:
        end_dt = start_dt + timedelta(minutes=settings.sync_default_event_minutes)
    else:
        end_dt = _coerce_datetime(notion_date.end)

    time_zone = notion_date.time_zone or settings.app_timezone
    return {
        "start": {"dateTime": start_dt.isoformat(), "timeZone": time_zone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": time_zone},
    }


def _coerce_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


def _sleep_after_calendar_write(settings: Settings) -> None:
    if settings.sync_calendar_write_delay_seconds > 0:
        sleep(settings.sync_calendar_write_delay_seconds)


def _is_page_object(value: dict[str, Any]) -> bool:
    return value.get("type") == "page" or value.get("object") == "page"


def _looks_like_full_page_payload(value: dict[str, Any]) -> bool:
    return _is_page_object(value) and isinstance(value.get("properties"), dict)


def mappings_for_page(page: dict[str, Any], settings: Settings) -> list[SyncMapping]:
    page_database_id = _page_database_id(page)
    page_data_source_id = _page_data_source_id(page)
    if not page_database_id and not page_data_source_id:
        return settings.resolved_sync_mappings()

    return [
        mapping
        for mapping in settings.resolved_sync_mappings()
        if _normalize_notion_id(mapping.notion_database_id)
        in {_normalize_notion_id(page_database_id), _normalize_notion_id(page_data_source_id)}
    ]


def page_matches_mapping_filters(page: dict[str, Any], mapping: SyncMapping) -> bool:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return False
    for mapping_filter in mapping.filters:
        prop = properties.get(mapping_filter.property)
        if not isinstance(prop, dict):
            return False
        if mapping_filter.type == "status_not_in":
            status = prop.get("status")
            status_name = status.get("name") if isinstance(status, dict) else None
            if status_name in mapping_filter.values:
                return False
    return True


def _page_database_id(page: dict[str, Any]) -> str | None:
    parent = page.get("parent")
    if not isinstance(parent, dict):
        return None
    value = parent.get("database_id")
    return value if isinstance(value, str) else None


def _page_data_source_id(page: dict[str, Any]) -> str | None:
    parent = page.get("parent")
    if not isinstance(parent, dict):
        return None
    value = parent.get("data_source_id")
    return value if isinstance(value, str) else None


def _normalize_notion_id(value: str | None) -> str:
    return (value or "").replace("-", "").lower()


def _add_sync_result(result: SyncAllResult, sync_result: SyncResult) -> None:
    result.results.append(sync_result)
    if sync_result.status == "created":
        result.created += 1
    elif sync_result.status == "updated":
        result.updated += 1
    elif sync_result.status == "deleted":
        result.deleted += 1
    elif sync_result.status == "unchanged":
        result.unchanged += 1
    elif sync_result.status == "skipped":
        result.skipped += 1
    elif sync_result.status == "error":
        result.errors += 1


def _resolve_clients(
    notion_client: NotionClientProtocol | None,
    gcal_client: GoogleCalendarClientProtocol | None,
    state_store: SyncStateStore | None,
    settings: Settings,
    mapping: SyncMapping | None = None,
) -> tuple[NotionClientProtocol, GoogleCalendarClientProtocol, SyncStateStore]:
    resolved_mapping = mapping or settings.resolved_sync_mappings()[0]
    if notion_client is None:
        from app.notion_client import NotionAPIClient

        notion_client = NotionAPIClient(settings, resolved_mapping)
    if gcal_client is None:
        from app.gcal_client import GoogleCalendarClient

        gcal_client = GoogleCalendarClient(settings, resolved_mapping.google_calendar_id)
    if state_store is None:
        state_store = SyncStateStore(settings)
    return notion_client, gcal_client, state_store


def _make_default_notion_client(settings: Settings) -> NotionClientProtocol:
    from app.notion_client import NotionAPIClient

    return NotionAPIClient(settings, settings.resolved_sync_mappings()[0])
