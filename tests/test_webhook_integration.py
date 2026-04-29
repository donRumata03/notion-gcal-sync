from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
import app.main as main_module
from app.sync import extract_page_ids_from_webhook, extract_page_payloads_from_webhook


SAMPLE_WEBHOOK_PAYLOAD = {
    "source": {
        "type": "automation",
        "automation_id": "3512d7fd-cb12-80de-bf49-004d30cc82bd",
    },
    "data": {
        "object": "page",
        "id": "3512d7fd-cb12-807d-b021-d75e56e170e3",
        "created_time": "2026-04-29T15:23:00.000Z",
        "last_edited_time": "2026-04-29T15:25:00.000Z",
        "in_trash": False,
        "is_archived": False,
        "properties": {
            "Name": {
                "id": "title",
                "type": "title",
                "title": [
                    {
                        "plain_text": "sadfadsf",
                    }
                ],
            },
            "Date/time": {
                "id": "d67dd048-bbb0-4274-8535-ddd41028ab03",
                "type": "date",
                "date": {
                    "start": "2026-04-29",
                    "end": None,
                    "time_zone": None,
                },
            },
        },
        "url": "https://app.notion.com/p/sadfadsf-3512d7fdcb12807db021d75e56e170e3",
    },
}


def _settings() -> Settings:
    return Settings(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="db",
        GOOGLE_CLIENT_ID="client",
        GOOGLE_CLIENT_SECRET="secret",
        GOOGLE_REFRESH_TOKEN="refresh",
        GOOGLE_CALENDAR_ID="primary",
    )


def test_extract_page_ids_from_webhook_handles_notion_page_object_payload() -> None:
    assert extract_page_ids_from_webhook(SAMPLE_WEBHOOK_PAYLOAD) == {"3512d7fd-cb12-807d-b021-d75e56e170e3"}


def test_extract_page_payloads_from_webhook_returns_inline_page_data() -> None:
    page_payloads = extract_page_payloads_from_webhook(SAMPLE_WEBHOOK_PAYLOAD)

    assert list(page_payloads) == ["3512d7fd-cb12-807d-b021-d75e56e170e3"]
    assert page_payloads["3512d7fd-cb12-807d-b021-d75e56e170e3"]["properties"]["Name"]["title"][0]["plain_text"] == "sadfadsf"


def test_notion_webhook_processes_inline_page_payload(monkeypatch) -> None:
    received_page_ids: list[str] = []

    monkeypatch.setattr(main_module, "get_settings", _settings)
    monkeypatch.setattr(main_module, "_background_sync_page", lambda page_id: (_ for _ in ()).throw(AssertionError(page_id)))
    monkeypatch.setattr(main_module, "_background_sync_all", lambda: (_ for _ in ()).throw(AssertionError("sync_all should not run")))
    monkeypatch.setattr(main_module, "_background_sync_page_payload", lambda page: received_page_ids.append(page["id"]))

    client = TestClient(app)
    response = client.post("/notion-webhook", json=deepcopy(SAMPLE_WEBHOOK_PAYLOAD))

    assert response.status_code == 200
    assert response.json() == {"ok": True, "queued_pages": 1}
    assert received_page_ids == ["3512d7fd-cb12-807d-b021-d75e56e170e3"]
