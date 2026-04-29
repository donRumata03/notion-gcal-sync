from __future__ import annotations

from app.config import Settings
from app.state_store import FirestoreStateStore, PostgresStateStore, SQLiteStateStore, SyncStateStore


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


def test_sync_state_store_uses_firestore_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(FirestoreStateStore, "_make_client", lambda self, settings: FakeFirestoreClient())

    store = SyncStateStore(_settings(STATE_BACKEND="firestore", FIRESTORE_PROJECT_ID="project-id"))

    assert isinstance(store.backend, FirestoreStateStore)

    store.upsert_success("page-1", "event-1", "hash-1", "https://calendar.test/event-1", mapping_id="main")
    record = store.get_record("page-1", mapping_id="main")

    assert record is not None
    assert record.page_id == "page-1"
    assert record.event_id == "event-1"
    assert record.sync_hash == "hash-1"
    assert record.calendar_url == "https://calendar.test/event-1"
    assert store.list_page_ids(mapping_id="main") == ["page-1"]


def test_firestore_state_store_keeps_mapping_scoped_keys(monkeypatch) -> None:
    monkeypatch.setattr(FirestoreStateStore, "_make_client", lambda self, settings: FakeFirestoreClient())
    store = SyncStateStore(_settings(STATE_BACKEND="firestore"))

    store.upsert_success("page-1", "event-main", "hash-main", mapping_id="main")
    store.upsert_success("page-1", "event-learning", "hash-learning", mapping_id="learning")

    main_record = store.get_record("page-1", mapping_id="main")
    learning_record = store.get_record("page-1", mapping_id="learning")

    assert main_record is not None
    assert learning_record is not None
    assert main_record.event_id == "event-main"
    assert learning_record.event_id == "event-learning"
    assert store.list_page_ids(mapping_id="main") == ["page-1"]
    assert store.list_page_ids(mapping_id="learning") == ["page-1"]


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeFirestoreCollection] = {}

    def collection(self, name: str) -> "FakeFirestoreCollection":
        return self.collections.setdefault(name, FakeFirestoreCollection())


class FakeFirestoreCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    def document(self, doc_id: str) -> "FakeFirestoreDocument":
        return FakeFirestoreDocument(self.documents, doc_id)

    def where(self, field: str, operator: str, value: object) -> "FakeFirestoreQuery":
        assert operator == "=="
        return FakeFirestoreQuery(self.documents, field, value)


class FakeFirestoreDocument:
    def __init__(self, documents: dict[str, dict], doc_id: str) -> None:
        self.documents = documents
        self.doc_id = doc_id

    def get(self) -> "FakeFirestoreSnapshot":
        return FakeFirestoreSnapshot(self.documents.get(self.doc_id))

    def set(self, data: dict, merge: bool = False) -> None:
        if merge:
            existing = self.documents.get(self.doc_id, {})
            self.documents[self.doc_id] = {**existing, **data}
        else:
            self.documents[self.doc_id] = data

    def delete(self) -> None:
        self.documents.pop(self.doc_id, None)


class FakeFirestoreQuery:
    def __init__(self, documents: dict[str, dict], field: str, value: object) -> None:
        self.documents = documents
        self.field = field
        self.value = value

    def stream(self) -> list["FakeFirestoreSnapshot"]:
        return [FakeFirestoreSnapshot(data) for data in self.documents.values() if data.get(self.field) == self.value]


class FakeFirestoreSnapshot:
    def __init__(self, data: dict | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict:
        return dict(self._data or {})
