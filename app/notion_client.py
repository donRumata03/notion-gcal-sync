from __future__ import annotations

from functools import cached_property
from typing import Any

from notion_client import Client
from notion_client.errors import APIResponseError

from app.config import Settings, SyncMapping, get_settings
from app.exceptions import NotionSchemaError
from app.logging_utils import get_logger
from app.models import get_checkbox, get_date, get_number, get_rich_text, get_status, get_title


NOTION_VERSION = "2026-03-11"
logger = get_logger(__name__)


class NotionAPIClient:
    def __init__(self, settings: Settings | None = None, mapping: SyncMapping | None = None) -> None:
        self.settings = settings or get_settings()
        self.mapping = mapping or self.settings.resolved_sync_mappings()[0]
        self.client = Client(auth=self.settings.notion_token, notion_version=NOTION_VERSION)

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self.client.pages.retrieve(page_id=page_id)

    def query_database_for_sync_candidates(self) -> list[dict[str, Any]]:
        self._validate_required_schema()

        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(pages) < self.settings.sync_max_pages:
            remaining = self.settings.sync_max_pages - len(pages)
            payload: dict[str, Any] = {
                "data_source_id": self.data_source_id,
                "page_size": min(remaining, 100),
                "result_type": "page",
                "filter": self._build_query_filter(),
            }
            if cursor:
                payload["start_cursor"] = cursor

            response = self.client.data_sources.query(**payload)
            results = [item for item in response.get("results", []) if item.get("object") == "page"]
            pages.extend(results)

            if not response.get("has_more") or not response.get("next_cursor"):
                break
            cursor = response["next_cursor"]

        return pages

    @cached_property
    def schema(self) -> dict[str, dict[str, Any]]:
        data_source = self.client.data_sources.retrieve(data_source_id=self.data_source_id)
        return data_source.get("properties", {})

    @cached_property
    def data_source_id(self) -> str:
        raw_id = self.mapping.notion_database_id
        if raw_id is None:
            raise NotionSchemaError("The sync mapping is missing notion_database_id.")

        try:
            self.client.data_sources.retrieve(data_source_id=raw_id)
            return raw_id
        except APIResponseError:
            pass

        database = self.client.databases.retrieve(database_id=raw_id)
        data_sources = database.get("data_sources", [])
        if not data_sources:
            raise NotionSchemaError("The configured Notion database does not expose any data sources.")

        if len(data_sources) > 1:
            logger.warning("Multiple data sources found under the configured database; using the first one.")
        return data_sources[0]["id"]

    def _validate_required_schema(self) -> None:
        required_properties = {self.mapping.title_property, self.mapping.date_property}
        required_properties.update(mapping_filter.property for mapping_filter in self.mapping.filters)
        missing = sorted(name for name in required_properties if name not in self.schema)
        if missing:
            raise NotionSchemaError(f"Missing required Notion properties: {', '.join(missing)}")

    def _build_query_filter(self) -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"property": self.mapping.date_property, "date": {"is_not_empty": True}},
        ]
        for mapping_filter in self.mapping.filters:
            if mapping_filter.type == "status_not_in":
                for value in mapping_filter.values:
                    filters.append({"property": mapping_filter.property, "status": {"does_not_equal": value}})
        if len(filters) == 1:
            return filters[0]
        return {"and": filters}


__all__ = [
    "NotionAPIClient",
    "get_checkbox",
    "get_date",
    "get_number",
    "get_rich_text",
    "get_status",
    "get_title",
]
