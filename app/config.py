import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DoneBehavior = Literal["delete", "keep", "mark_done"]
StateBackend = Literal["auto", "sqlite", "firestore", "postgres"]


class MappingFilter(BaseModel):
    property: str
    type: Literal["status_not_in"] = "status_not_in"
    values: list[str]


class SyncMapping(BaseModel):
    id: str | None = None
    notion_database_id: str | None = None
    notion_database_ids: list[str] | None = None
    google_calendar_id: str
    title_property: str = "Name"
    date_property: str = "Date/time"
    status_property: str | None = "Status"
    sync_to_calendar_property: str | None = None
    duration_minutes_property: str | None = None
    filters: list[MappingFilter] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_database_ids(self) -> "SyncMapping":
        if not self.notion_database_id and not self.notion_database_ids:
            raise ValueError("Set notion_database_id or notion_database_ids.")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    notion_token: str = Field(..., alias="NOTION_TOKEN")
    notion_database_id: str = Field(..., alias="NOTION_DATABASE_ID")
    google_client_id: str | None = Field(default=None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: str | None = Field(default=None, alias="GOOGLE_CLIENT_SECRET")
    google_client_secret_file: str | None = Field(default=None, alias="GOOGLE_CLIENT_SECRET_FILE")
    google_refresh_token: str = Field(..., alias="GOOGLE_REFRESH_TOKEN")
    google_calendar_id: str = Field(..., alias="GOOGLE_CALENDAR_ID")

    notion_webhook_secret: str | None = Field(default=None, alias="NOTION_WEBHOOK_SECRET")
    app_timezone: str = Field(default="UTC", alias="APP_TIMEZONE")
    sync_done_behavior: DoneBehavior = Field(default="delete", alias="SYNC_DONE_BEHAVIOR")
    sync_default_event_minutes: int = Field(default=30, alias="SYNC_DEFAULT_EVENT_MINUTES")
    sync_max_pages: int = Field(default=100, alias="SYNC_MAX_PAGES")
    sync_calendar_write_delay_seconds: float = Field(default=0.2, alias="SYNC_CALENDAR_WRITE_DELAY_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    state_backend: StateBackend = Field(default="auto", alias="STATE_BACKEND")
    state_db_path: str = Field(default="./data/sync-state.sqlite3", alias="STATE_DB_PATH")
    firestore_project_id: str | None = Field(default=None, alias="FIRESTORE_PROJECT_ID")
    firestore_database: str | None = Field(default=None, alias="FIRESTORE_DATABASE")
    firestore_collection: str = Field(default="sync_state", alias="FIRESTORE_COLLECTION")
    state_database_url: str | None = Field(default=None, alias="STATE_DATABASE_URL")
    cloud_sql_connection_name: str | None = Field(default=None, alias="CLOUD_SQL_CONNECTION_NAME")
    cloud_sql_database: str | None = Field(default=None, alias="CLOUD_SQL_DATABASE")
    cloud_sql_user: str | None = Field(default=None, alias="CLOUD_SQL_USER")
    cloud_sql_password: str | None = Field(default=None, alias="CLOUD_SQL_PASSWORD")

    notion_prop_title: str = Field(default="Name", alias="NOTION_PROP_TITLE")
    notion_prop_date: str = Field(default="Date/time", alias="NOTION_PROP_DATE")
    notion_prop_status: str = Field(default="Status", alias="NOTION_PROP_STATUS")
    notion_prop_sync_to_calendar: str | None = Field(default=None, alias="NOTION_PROP_SYNC_TO_CALENDAR")
    notion_prop_duration_minutes: str | None = Field(default=None, alias="NOTION_PROP_DURATION_MINUTES")
    sync_mappings: list[SyncMapping] | None = Field(default=None, alias="SYNC_MAPPINGS")

    @model_validator(mode="after")
    def resolve_google_oauth_client(self) -> "Settings":
        if self.google_client_id and self.google_client_secret:
            return self

        if not self.google_client_secret_file:
            raise ValueError(
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, or set GOOGLE_CLIENT_SECRET_FILE to a client_secret JSON file."
            )

        payload = json.loads(Path(self.google_client_secret_file).read_text(encoding="utf-8"))
        client_config = payload.get("installed") or payload.get("web")
        if not isinstance(client_config, dict):
            raise ValueError("GOOGLE_CLIENT_SECRET_FILE must contain an 'installed' or 'web' OAuth client config.")

        client_id = client_config.get("client_id")
        client_secret = client_config.get("client_secret")
        if not client_id or not client_secret:
            raise ValueError("GOOGLE_CLIENT_SECRET_FILE is missing client_id or client_secret.")

        self.google_client_id = client_id
        self.google_client_secret = client_secret
        return self

    def resolved_sync_mappings(self) -> list[SyncMapping]:
        if self.sync_mappings:
            return [
                expanded_mapping
                for index, mapping in enumerate(self.sync_mappings)
                for expanded_mapping in _expand_mapping(_with_mapping_id(mapping, index))
            ]

        return [
            SyncMapping(
                id="default",
                notion_database_id=self.notion_database_id,
                google_calendar_id=self.google_calendar_id,
                title_property=self.notion_prop_title,
                date_property=self.notion_prop_date,
                status_property=self.notion_prop_status,
                sync_to_calendar_property=self.notion_prop_sync_to_calendar,
                duration_minutes_property=self.notion_prop_duration_minutes,
            )
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _with_mapping_id(mapping: SyncMapping, index: int) -> SyncMapping:
    if mapping.id:
        return mapping
    return mapping.model_copy(update={"id": f"mapping-{index + 1}"})


def _expand_mapping(mapping: SyncMapping) -> list[SyncMapping]:
    database_ids = mapping.notion_database_ids or ([mapping.notion_database_id] if mapping.notion_database_id else [])
    if len(database_ids) <= 1:
        return [mapping.model_copy(update={"notion_database_id": database_ids[0], "notion_database_ids": None})]
    return [
        mapping.model_copy(
            update={
                "id": f"{mapping.id}-{index + 1}",
                "notion_database_id": database_id,
                "notion_database_ids": None,
            }
        )
        for index, database_id in enumerate(database_ids)
    ]
