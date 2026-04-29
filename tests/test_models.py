from datetime import date, datetime

from app.models import get_checkbox, get_date, get_number, get_rich_text, get_status, get_title, parse_notion_page


def _base_page(properties: dict) -> dict:
    return {
        "id": "page-123",
        "url": "https://www.notion.so/page-123",
        "archived": False,
        "properties": properties,
    }


def test_parse_title() -> None:
    page = _base_page(
        {
            "Name": {"type": "title", "title": [{"plain_text": "Write docs"}]},
        }
    )

    assert get_title(page) == "Write docs"


def test_parse_all_day_date() -> None:
    page = _base_page(
        {
            "Date/time": {"type": "date", "date": {"start": "2026-04-29", "end": None, "time_zone": None}},
        }
    )

    notion_date = get_date(page, "Date/time")

    assert notion_date is not None
    assert notion_date.start == date(2026, 4, 29)
    assert notion_date.end is None
    assert notion_date.is_all_day is True


def test_parse_timed_date() -> None:
    page = _base_page(
        {
            "Date/time": {
                "type": "date",
                "date": {
                    "start": "2026-04-29T09:00:00+03:00",
                    "end": "2026-04-29T10:15:00+03:00",
                    "time_zone": "Europe/Moscow",
                },
            },
        }
    )

    notion_date = get_date(page, "Date/time")

    assert notion_date is not None
    assert notion_date.start == datetime.fromisoformat("2026-04-29T09:00:00+03:00")
    assert notion_date.end == datetime.fromisoformat("2026-04-29T10:15:00+03:00")
    assert notion_date.time_zone == "Europe/Moscow"
    assert notion_date.is_all_day is False


def test_missing_optional_properties_do_not_crash() -> None:
    page = _base_page(
        {
            "Name": {"type": "title", "title": [{"plain_text": "Task"}]},
            "Date/time": {"type": "date", "date": None},
        }
    )

    task = parse_notion_page(page)

    assert task.title == "Task"
    assert task.date is None
    assert task.status is None
    assert task.sync_to_calendar is None
    assert task.google_event_id is None
    assert get_checkbox(page) is None
    assert get_status(page) is None
    assert get_rich_text(page, "Sync Hash") is None


def test_parse_duration_minutes() -> None:
    page = _base_page(
        {
            "Время, минут": {"type": "number", "number": 45},
        }
    )

    assert get_number(page, "Время, минут") == 45
