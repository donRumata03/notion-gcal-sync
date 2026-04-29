# Notion -> Google Calendar Sync

A free, self-hosted Notion to Google Calendar sync service. Unlike paid hosted tools such as https://2sync.com/, this repo is yours to run, inspect, extend, and deploy without a subscription.

It syncs existing database pages and future edits from Notion into Google Calendar. Notion stays read-only: the app never writes sync metadata back to Notion.

## What It Syncs
- Notion pages with a configured date property.
- All-day dates, date ranges, exact start times, and exact start/end times.
- One Notion database to one calendar, or many databases to many calendars.
- Optional per-route filters, for example "only sync Learning deadlines where `Done?!` is not completed".

Each Google Calendar event stores the Notion page ID in:
- `extendedProperties.private.notionPageId`
- the event description as `Page ID: ...`

## Quick Start
```bash
uv sync
copy .env.example .env
uv run python scripts/get_google_refresh_token.py
uv run uvicorn app.main:app --reload
```

Trigger a full sync:
```bash
curl -X POST http://127.0.0.1:8000/sync-all
```

`/sync-all` handles pre-existing pages. Webhooks handle later edits.

## Basic Config
Required:
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_CALENDAR_ID`

Google OAuth client credentials can be configured as:
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
- or `GOOGLE_CLIENT_SECRET_FILE` pointing at a local `client_secret...json`

Default single-route behavior:
```text
NOTION_DATABASE_ID=your_notion_database_id
GOOGLE_CALENDAR_ID=primary
NOTION_PROP_TITLE=Name
NOTION_PROP_DATE=Date/time
```

## Multi-Route Config
Set `SYNC_MAPPINGS` to route many Notion databases to many Google Calendars. When this is set, it replaces the single `NOTION_DATABASE_ID -> GOOGLE_CALENDAR_ID` route.

Example:
```json
[
  {
    "id": "main",
    "notion_database_ids": ["32573b18d05641f3a559a4aa61bd6fea"],
    "google_calendar_id": "primary",
    "title_property": "Name",
    "date_property": "Date/time"
  },
  {
    "id": "learning-deadlines",
    "notion_database_ids": ["3b35389c4b674936b4fdf4dfb928d2c2"],
    "google_calendar_id": "84c2bf5ac1d365f289013c323fdd286b04967e938d340903503928a39b28f776@group.calendar.google.com",
    "title_property": "Name",
    "date_property": "Deadline",
    "filters": [
      {"property": "Done?!", "type": "status_not_in", "values": ["Done", "Complete"]}
    ]
  }
]
```

The first route syncs the main task database by `Date/time`. The second route syncs Learning deadlines by `Deadline`, but excludes completed tasks based on `Done?!`.

## Webhook
Webhook endpoint:
```text
POST /notion-webhook
```

If the webhook contains a full page object, the app syncs from that payload. If it contains only page IDs, the app fetches the current page from Notion.

Configure the same webhook URL for every Notion database used in `SYNC_MAPPINGS`.

## State Storage
Local default:
```text
STATE_BACKEND=sqlite
STATE_DB_PATH=./data/sync-state.sqlite3
```

Cloud Run production setup uses Firestore:
```text
STATE_BACKEND=firestore
FIRESTORE_PROJECT_ID=calendar-sync-494800
FIRESTORE_COLLECTION=sync_state
```

The app stores one document per mapping-aware Notion page key. The document keeps the public Notion page id, mapping id, Google Calendar event id, sync hash, calendar URL, last sync timestamp, and last error.

Legacy Postgres/Cloud SQL remains available for migration or rollback only:
```text
STATE_DATABASE_URL=postgresql://sync_user:password@127.0.0.1:5432/notion_gcal_sync
```

## Cloud Run Secrets
Use Secret Manager for:
- `NOTION_TOKEN`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `NOTION_WEBHOOK_SECRET` only if your webhook sender signs requests with `X-Notion-Signature`

Use normal Cloud Run env vars for non-secret config such as `STATE_BACKEND`, `FIRESTORE_PROJECT_ID`, `FIRESTORE_COLLECTION`, `SYNC_MAPPINGS`, `APP_TIMEZONE`, and `SYNC_MAX_PAGES`.
