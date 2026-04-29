from datetime import date, datetime

from app.config import MappingFilter, Settings, SyncMapping
from app.models import CalendarEventResult, NotionDate, NotionTask
from app.state_store import SyncStateRecord
from app.sync import build_calendar_event, decide_sync_action, sync_page_object, sync_page_payload
from app.sync import page_matches_mapping_filters


class FakeNotionClient:
    def get_page(self, page_id: str) -> dict:
        raise NotImplementedError

    def query_database_for_sync_candidates(self) -> list[dict]:
        return []


class FakeGoogleCalendarClient:
    def __init__(self) -> None:
        self.deleted_ids: list[str] = []

    def create_event(self, event_body: dict) -> CalendarEventResult:
        return CalendarEventResult(event_id="created-1", html_link="https://calendar.test/e/created-1", raw=event_body)

    def update_event(self, event_id: str, event_body: dict) -> CalendarEventResult:
        return CalendarEventResult(event_id=event_id, html_link=f"https://calendar.test/e/{event_id}", raw=event_body)

    def delete_event(self, event_id: str) -> None:
        self.deleted_ids.append(event_id)


class FakeStateStore:
    def __init__(self) -> None:
        self.records: dict[str, SyncStateRecord] = {}
        self.error_calls: list[dict[str, str]] = []
        self.deleted_page_ids: list[str] = []

    def get_record(self, page_id: str, mapping_id: str = "default") -> SyncStateRecord | None:
        return self.records.get(_state_key(page_id, mapping_id))

    def list_page_ids(self, mapping_id: str = "default") -> list[str]:
        prefix = "" if mapping_id == "default" else f"{mapping_id}:"
        return sorted(key.removeprefix(prefix) for key in self.records if key.startswith(prefix))

    def upsert_success(
        self, page_id: str, event_id: str, sync_hash: str, calendar_url: str | None = None, mapping_id: str = "default"
    ) -> None:
        self.records[_state_key(page_id, mapping_id)] = SyncStateRecord(
            page_id=page_id,
            event_id=event_id,
            sync_hash=sync_hash,
            calendar_url=calendar_url,
        )

    def upsert_error(self, page_id: str, error_message: str, mapping_id: str = "default") -> None:
        self.error_calls.append({"page_id": page_id, "error_message": error_message})

    def delete_record(self, page_id: str, mapping_id: str = "default") -> None:
        self.deleted_page_ids.append(page_id)
        self.records.pop(_state_key(page_id, mapping_id), None)


def _state_key(page_id: str, mapping_id: str) -> str:
    if mapping_id == "default":
        return page_id
    return f"{mapping_id}:{page_id}"


def _settings(done_behavior: str = "delete") -> Settings:
    return Settings(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="db",
        GOOGLE_CLIENT_ID="client",
        GOOGLE_CLIENT_SECRET="secret",
        GOOGLE_REFRESH_TOKEN="refresh",
        GOOGLE_CALENDAR_ID="primary",
        SYNC_DONE_BEHAVIOR=done_behavior,
    )


def _task(**overrides: object) -> NotionTask:
    base = NotionTask(
        page_id="page-1",
        notion_url="https://www.notion.so/page-1",
        title="Task",
        status="In Progress",
        sync_to_calendar=True,
        date=NotionDate(start=date(2026, 4, 29), end=None, is_all_day=True),
        google_event_id=None,
        sync_hash=None,
        is_archived=False,
    )
    return base.model_copy(update=overrides)


def _page_from_task(task: NotionTask) -> dict:
    properties = {
        "Name": {"type": "title", "title": [{"plain_text": task.title}]},
        "Date/time": {
            "type": "date",
            "date": None
            if task.date is None
            else {
                "start": task.date.start.isoformat(),
                "end": task.date.end.isoformat() if task.date.end is not None else None,
                "time_zone": task.date.time_zone,
            },
        },
        "Status": {
            "type": "status",
            "status": None if task.status is None else {"name": task.status},
        },
    }
    if task.sync_to_calendar is not None:
        properties["Sync to Calendar"] = {"type": "checkbox", "checkbox": task.sync_to_calendar}
    if task.duration_minutes is not None:
        properties["Время, минут"] = {"type": "number", "number": task.duration_minutes}
    return {
        "id": task.page_id,
        "url": task.notion_url,
        "archived": task.is_archived,
        "properties": properties,
    }


def test_all_day_single_date_maps_to_exclusive_next_day_end() -> None:
    event = build_calendar_event(_task(), _settings())

    assert event["start"] == {"date": "2026-04-29"}
    assert event["end"] == {"date": "2026-04-30"}
    assert event["extendedProperties"]["private"]["notionPageId"] == "page-1"


def test_timed_event_without_end_gets_default_duration() -> None:
    task = _task(
        date=NotionDate(
            start=datetime.fromisoformat("2026-04-29T09:00:00+03:00"),
            end=None,
            time_zone="Europe/Moscow",
            is_all_day=False,
        )
    )

    event = build_calendar_event(task, _settings())

    assert event["start"]["dateTime"] == "2026-04-29T09:00:00+03:00"
    assert event["end"]["dateTime"] == "2026-04-29T09:30:00+03:00"
    assert event["start"]["timeZone"] == "Europe/Moscow"


def test_timed_event_without_end_ignores_optional_duration_property() -> None:
    task = _task(
        date=NotionDate(
            start=datetime.fromisoformat("2026-04-29T09:00:00+03:00"),
            end=None,
            time_zone="Europe/Moscow",
            is_all_day=False,
        ),
        duration_minutes=45,
    )

    event = build_calendar_event(task, _settings())

    assert event["end"]["dateTime"] == "2026-04-29T09:30:00+03:00"


def test_done_task_with_delete_behavior_decides_delete() -> None:
    action = decide_sync_action(_task(status="Done", google_event_id="evt-1"), _settings(done_behavior="delete"))

    assert action == "upsert_event"


def test_task_with_no_date_decides_delete_if_event_exists_otherwise_skip() -> None:
    with_event = decide_sync_action(_task(date=None, google_event_id="evt-1"), _settings())
    without_event = decide_sync_action(_task(date=None, google_event_id=None), _settings())

    assert with_event == "delete_event"
    assert without_event == "skip_without_event"


def test_sync_checkbox_is_ignored_when_date_exists() -> None:
    action = decide_sync_action(_task(sync_to_calendar=False, google_event_id="evt-1"), _settings())

    assert action == "upsert_event"


def test_sync_page_object_deletes_existing_event_for_task_without_date() -> None:
    notion_client = FakeNotionClient()
    gcal_client = FakeGoogleCalendarClient()
    state_store = FakeStateStore()
    state_store.records["page-1"] = SyncStateRecord(page_id="page-1", event_id="evt-1", sync_hash="hash-1")
    page = _page_from_task(_task(date=None, google_event_id="evt-1"))

    result = sync_page_object(page, notion_client, gcal_client, state_store, _settings())

    assert result.status == "deleted"
    assert gcal_client.deleted_ids == ["evt-1"]
    assert state_store.deleted_page_ids == ["page-1"]


def test_sync_page_payload_uses_inline_page_without_fetching() -> None:
    notion_client = FakeNotionClient()
    gcal_client = FakeGoogleCalendarClient()
    state_store = FakeStateStore()
    page = _page_from_task(_task(title="Inline payload"))

    result = sync_page_payload(page, notion_client, gcal_client, state_store, _settings())

    assert result.created == 1
    assert state_store.records["page-1"].event_id == "created-1"


def test_status_not_in_mapping_filter_excludes_completed_status() -> None:
    mapping = SyncMapping(
        id="learning",
        notion_database_id="db",
        google_calendar_id="learning-calendar",
        date_property="Deadline",
        filters=[MappingFilter(property="Done?!", values=["Done", "Completed"])],
    )
    page = _page_from_task(_task())
    page["properties"]["Done?!"] = {"type": "status", "status": {"name": "Done"}}

    assert page_matches_mapping_filters(page, mapping) is False


def test_status_not_in_mapping_filter_allows_not_started_status() -> None:
    mapping = SyncMapping(
        id="learning",
        notion_database_id="db",
        google_calendar_id="learning-calendar",
        date_property="Deadline",
        filters=[MappingFilter(property="Done?!", values=["Done", "Completed"])],
    )
    page = _page_from_task(_task())
    page["properties"]["Done?!"] = {"type": "status", "status": {"name": "Not started"}}

    assert page_matches_mapping_filters(page, mapping) is True


def test_sync_mapping_expands_database_id_arrays() -> None:
    settings = _settings().model_copy(
        update={
            "sync_mappings": [
                SyncMapping(
                    id="primary",
                    notion_database_ids=["db-1", "db-2"],
                    google_calendar_id="calendar-1",
                    date_property="Date/time",
                )
            ]
        }
    )

    mappings = settings.resolved_sync_mappings()

    assert [mapping.id for mapping in mappings] == ["primary-1", "primary-2"]
    assert [mapping.notion_database_id for mapping in mappings] == ["db-1", "db-2"]
    assert [mapping.google_calendar_id for mapping in mappings] == ["calendar-1", "calendar-1"]
