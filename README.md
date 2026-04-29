# Notion -> Google Calendar Sync

One-way sync from a Notion database into Google Calendar.

The app treats Notion as read-only:
- it never writes sync metadata back to Notion
- the sync key is the Notion `page_id`
- Google event IDs, sync hashes, errors, and last sync timestamps live in the configured state database

## Notion properties used
- `Name` (`title`)
- `Date/time` (`date`)

Only pages where `Date/time` is not empty are synced to Google Calendar.

Supported Notion date shapes:
- all-day date with only a start day
- all-day date range
- exact start time without an end time
- exact start and end time

If a timed Notion date has no end time, the app uses `SYNC_DEFAULT_EVENT_MINUTES`.

## Local setup with uv
```bash
uv sync
copy .env.example .env
uv run uvicorn app.main:app --reload
```

## Environment
Required:
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_CALENDAR_ID`

For Google OAuth client credentials, use either:
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
- or `GOOGLE_CLIENT_SECRET_FILE` pointing at your downloaded `client_secret...json`

Optional:
- `APP_TIMEZONE`
- `SYNC_DEFAULT_EVENT_MINUTES`
- `SYNC_MAX_PAGES`
- `SYNC_CALENDAR_WRITE_DELAY_SECONDS`
- `STATE_DB_PATH`
- `STATE_DATABASE_URL`
- `CLOUD_SQL_CONNECTION_NAME`
- `CLOUD_SQL_DATABASE`
- `CLOUD_SQL_USER`
- `CLOUD_SQL_PASSWORD`

## Get a Google refresh token
Using your existing OAuth client JSON:
```bash
uv run python scripts/get_google_refresh_token.py --client-secret-file ./client_secret_xxx.json
```

Or set `GOOGLE_CLIENT_SECRET_FILE` in `.env` and run:
```bash
uv run python scripts/get_google_refresh_token.py
```

## Trigger sync
```bash
curl -X POST http://127.0.0.1:8000/sync-all
```

## Webhook
Endpoint:
```text
POST /notion-webhook
```

If the webhook payload contains the full page object, the app syncs directly from that payload.
If the webhook only contains page identifiers, the app fetches the current page from Notion before syncing.

## Google Calendar event metadata
Each synced event stores the Notion page ID in two places:
- `extendedProperties.private.notionPageId`
- the event description as `Page ID: ...`

## State storage
By default the app uses local SQLite:
```text
./data/sync-state.sqlite3
```

For Cloud Run, use Cloud SQL for PostgreSQL instead of local SQLite. Attach the Cloud SQL instance to the Cloud Run service and set:
```text
CLOUD_SQL_CONNECTION_NAME=project:region:instance
CLOUD_SQL_DATABASE=notion_gcal_sync
CLOUD_SQL_USER=sync_user
CLOUD_SQL_PASSWORD=...
```

The app creates the `sync_state` table automatically.

For local Postgres or the Cloud SQL Auth Proxy, set a direct URL instead:
```text
STATE_DATABASE_URL=postgresql://sync_user:password@127.0.0.1:5432/notion_gcal_sync
```

If neither `STATE_DATABASE_URL` nor `CLOUD_SQL_CONNECTION_NAME` is set, SQLite is used.

## Deploy on Cloud Run
Build and deploy from the repo, then configure runtime environment separately from git.

Recommended Cloud Run environment variables:
- `NOTION_DATABASE_ID`
- `GOOGLE_CALENDAR_ID`
- `APP_TIMEZONE`
- `SYNC_DEFAULT_EVENT_MINUTES`
- `SYNC_MAX_PAGES`
- `SYNC_CALENDAR_WRITE_DELAY_SECONDS`
- `CLOUD_SQL_CONNECTION_NAME`
- `CLOUD_SQL_DATABASE`
- `NOTION_PROP_TITLE`
- `NOTION_PROP_DATE`

Recommended Secret Manager secrets:
- `NOTION_TOKEN`
- `GOOGLE_REFRESH_TOKEN`
- either `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, or a mounted `GOOGLE_CLIENT_SECRET_FILE`
- `CLOUD_SQL_USER`
- `CLOUD_SQL_PASSWORD`
- `NOTION_WEBHOOK_SECRET` only if your webhook sender signs requests with `X-Notion-Signature`

Do not configure `STATE_DB_PATH` for production Cloud Run unless you intentionally want ephemeral local state. Local SQLite on Cloud Run can lose `page_id -> event_id` mappings on restarts or new revisions and recreate calendar events.
