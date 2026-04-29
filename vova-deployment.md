# Vova deployment notes

Ugly operational note. Do not put secret values here.

## Current GCP deployment

- GCP project id: `calendar-sync-494800`
- Region: `europe-west1`
- Cloud Run service name: `notion-gcall-sync`
- Latest deployed revision when this note was written: `notion-gcall-sync-00016-qf9`
- Runtime: Python app in Docker, started with `uv run uvicorn app.main:app`
- Cloud Run limits: 1 vCPU, 512 MiB RAM, max scale 3, no minimum instances configured
- Current storage backend: Cloud SQL for sync state
- Webhook endpoint: configured in Notion automations, exact URL intentionally omitted here

## Google Cloud resources

- Cloud Run service: `notion-gcall-sync`
- Cloud SQL instance: `notion-gcal-sync-db`
- Cloud SQL database: `notion_gcal_sync`
- Cloud SQL user: `sync_user`
- Secret Manager is used for runtime credentials/config

## Secret names and env names

Secret Manager secret names:

- `notion-token`
- `google-refresh-token`
- `google-client-id`
- `google-client-secret`
- `cloud-sql-password`
- `sync-mappings`

Runtime env/config names:

- `NOTION_TOKEN`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `SYNC_MAPPINGS`
- `CLOUD_SQL_CONNECTION_NAME`
- `CLOUD_SQL_DATABASE`
- `CLOUD_SQL_USER`
- `CLOUD_SQL_PASSWORD`
- `APP_TIMEZONE`
- `LOG_LEVEL`

Local-only credential/config files:

- `.env`
- `client_secret_*.json`

These are ignored by git.

## Sync mappings currently expected

- Task List database -> primary Google Calendar
  - Notion date property: `Date/time`
  - Notion title property: `Name`
- Learning deadlines database -> separate Google Calendar
  - Notion date property: `Deadline`
  - Notion title property: `Name`
  - Filter: `Done?!` status must not be `Done` or `Complete`

Exact database ids and calendar ids live in `SYNC_MAPPINGS`.

## Tools used by agents/operators

- `uv`: install dependencies, run tests, run the app
- `pytest`: local test suite via `uv run pytest`
- `gcloud`: deploy Cloud Run, update secrets, inspect logs/resources
- Google Cloud Build / Docker: build container image for Cloud Run
- Google Cloud Secret Manager: store runtime secrets
- Google Cloud SQL: store sync state
- Google Calendar API: create/update/delete/list calendar events
- Notion API: query data sources and retrieve pages

On this Windows machine, `gcloud` may need the full path:

```powershell
C:\Users\Vova\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd
```

## Duplicate handling

Google Calendar events store the Notion page id in:

```text
extendedProperties.private.notionPageId
```

The app also stores sync state in Cloud SQL. Current code checks Google Calendar by `notionPageId` before creating an event, so missing local state should not create another duplicate for the same page. If several matching events already exist, the sync keeps one and deletes the extra matches.

## Cost note

Cloud Run should be close to free for this workload if no minimum instances are configured. Cloud SQL is the main always-on cost. It is useful for robust cleanup when Notion fields are cleared, pages are completed, or webhook events are missed, but the app now also uses Google Calendar event metadata as a fallback identity source.
