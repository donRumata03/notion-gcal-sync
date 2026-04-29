from __future__ import annotations

from app.config import Settings
from app.state_store import PostgresStateStore, SQLiteStateStore, SyncStateStore


def _settings(**overrides: object) -> Settings:
    values = {
        "NOTION_TOKEN": "x",
        "NOTION_DATABASE_ID": "db",
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REFRESH_TOKEN": "refresh",
        "GOOGLE_CALENDAR_ID": "primary",
    }
    values.update(overrides)
    return Settings(**values)


def test_sync_state_store_uses_sqlite_by_default(tmp_path) -> None:
    store = SyncStateStore(_settings(STATE_DB_PATH=str(tmp_path / "state.sqlite3")))

    assert isinstance(store.backend, SQLiteStateStore)

    store.upsert_success("page-1", "event-1", "hash-1", "https://calendar.test/event-1")
    record = store.get_record("page-1")

    assert record is not None
    assert record.page_id == "page-1"
    assert record.event_id == "event-1"
    assert record.sync_hash == "hash-1"
    assert record.calendar_url == "https://calendar.test/event-1"
    assert record.last_error is None


def test_sync_state_store_uses_postgres_when_database_url_is_set(monkeypatch) -> None:
    monkeypatch.setattr(PostgresStateStore, "_initialize", lambda self: None)

    store = SyncStateStore(
        _settings(
            STATE_DATABASE_URL="postgresql://sync_user:secret@127.0.0.1:5432/notion_gcal_sync",
        )
    )

    assert isinstance(store.backend, PostgresStateStore)


def test_sync_state_store_uses_postgres_when_cloud_sql_is_set(monkeypatch) -> None:
    monkeypatch.setattr(PostgresStateStore, "_initialize", lambda self: None)

    store = SyncStateStore(
        _settings(
            CLOUD_SQL_CONNECTION_NAME="project:region:instance",
            CLOUD_SQL_DATABASE="notion_gcal_sync",
            CLOUD_SQL_USER="sync_user",
            CLOUD_SQL_PASSWORD="secret",
        )
    )

    assert isinstance(store.backend, PostgresStateStore)
