from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from pydantic import BaseModel

from app.config import Settings, get_settings


class SyncStateRecord(BaseModel):
    page_id: str
    event_id: str
    sync_hash: str | None = None
    calendar_url: str | None = None
    last_synced_at: str | None = None
    last_error: str | None = None


class StateStoreBackend(Protocol):
    def get_record(self, page_id: str) -> SyncStateRecord | None: ...

    def list_page_ids(self) -> list[str]: ...

    def upsert_success(self, page_id: str, event_id: str, sync_hash: str, calendar_url: str | None = None) -> None: ...

    def upsert_error(self, page_id: str, error_message: str) -> None: ...

    def delete_record(self, page_id: str) -> None: ...


class SyncStateStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.backend = create_state_store_backend(self.settings)

    def get_record(self, page_id: str) -> SyncStateRecord | None:
        return self.backend.get_record(page_id)

    def list_page_ids(self) -> list[str]:
        return self.backend.list_page_ids()

    def upsert_success(self, page_id: str, event_id: str, sync_hash: str, calendar_url: str | None = None) -> None:
        self.backend.upsert_success(page_id, event_id, sync_hash, calendar_url)

    def upsert_error(self, page_id: str, error_message: str) -> None:
        self.backend.upsert_error(page_id, error_message)

    def delete_record(self, page_id: str) -> None:
        self.backend.delete_record(page_id)


def create_state_store_backend(settings: Settings) -> StateStoreBackend:
    if settings.state_database_url or settings.cloud_sql_connection_name:
        return PostgresStateStore(settings)
    return SQLiteStateStore(settings)


class SQLiteStateStore:
    def __init__(self, settings: Settings) -> None:
        self.db_path = Path(settings.state_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get_record(self, page_id: str) -> SyncStateRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT page_id, event_id, sync_hash, calendar_url, last_synced_at, last_error
                FROM sync_state
                WHERE page_id = ?
                """,
                (page_id,),
            ).fetchone()
        if row is None:
            return None
        return SyncStateRecord.model_validate(dict(row))

    def list_page_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT page_id FROM sync_state ORDER BY page_id").fetchall()
        return [row["page_id"] for row in rows]

    def upsert_success(self, page_id: str, event_id: str, sync_hash: str, calendar_url: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (page_id, event_id, sync_hash, calendar_url, last_synced_at, last_error)
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(page_id) DO UPDATE SET
                    event_id = excluded.event_id,
                    sync_hash = excluded.sync_hash,
                    calendar_url = excluded.calendar_url,
                    last_synced_at = excluded.last_synced_at,
                    last_error = NULL
                """,
                (page_id, event_id, sync_hash, calendar_url, _utc_now_timestamp()),
            )

    def upsert_error(self, page_id: str, error_message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (page_id, event_id, sync_hash, calendar_url, last_synced_at, last_error)
                VALUES (?, '', NULL, NULL, NULL, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    last_error = excluded.last_error
                """,
                (page_id, error_message[:1900]),
            )

    def delete_record(self, page_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sync_state WHERE page_id = ?", (page_id,))

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


class PostgresStateStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._initialize()

    def get_record(self, page_id: str) -> SyncStateRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT page_id, event_id, sync_hash, calendar_url, last_synced_at, last_error
                FROM sync_state
                WHERE page_id = %s
                """,
                (page_id,),
            ).fetchone()
        if row is None:
            return None
        return SyncStateRecord.model_validate(dict(row))

    def list_page_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT page_id FROM sync_state ORDER BY page_id").fetchall()
        return [row["page_id"] for row in rows]

    def upsert_success(self, page_id: str, event_id: str, sync_hash: str, calendar_url: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (page_id, event_id, sync_hash, calendar_url, last_synced_at, last_error)
                VALUES (%s, %s, %s, %s, %s, NULL)
                ON CONFLICT(page_id) DO UPDATE SET
                    event_id = excluded.event_id,
                    sync_hash = excluded.sync_hash,
                    calendar_url = excluded.calendar_url,
                    last_synced_at = excluded.last_synced_at,
                    last_error = NULL
                """,
                (page_id, event_id, sync_hash, calendar_url, _utc_now_timestamp()),
            )

    def upsert_error(self, page_id: str, error_message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (page_id, event_id, sync_hash, calendar_url, last_synced_at, last_error)
                VALUES (%s, '', NULL, NULL, NULL, %s)
                ON CONFLICT(page_id) DO UPDATE SET
                    last_error = excluded.last_error
                """,
                (page_id, error_message[:1900]),
            )

    def delete_record(self, page_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sync_state WHERE page_id = %s", (page_id,))

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(**self._connection_kwargs(), row_factory=dict_row)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _connection_kwargs(self) -> dict[str, Any]:
        if self.settings.state_database_url:
            return {"conninfo": self.settings.state_database_url}

        missing = [
            name
            for name, value in [
                ("CLOUD_SQL_DATABASE", self.settings.cloud_sql_database),
                ("CLOUD_SQL_USER", self.settings.cloud_sql_user),
            ]
            if not value
        ]
        if missing:
            raise ValueError(f"Missing Cloud SQL state store settings: {', '.join(missing)}")

        return {
            "host": f"/cloudsql/{self.settings.cloud_sql_connection_name}",
            "dbname": self.settings.cloud_sql_database,
            "user": self.settings.cloud_sql_user,
            "password": self.settings.cloud_sql_password,
        }


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sync_state (
    page_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    sync_hash TEXT,
    calendar_url TEXT,
    last_synced_at TEXT,
    last_error TEXT
)
"""


def _utc_now_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
