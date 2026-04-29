from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.config import get_settings
from app.logging_utils import configure_logging, get_logger
from app.sync import extract_page_ids_from_webhook, extract_page_payloads_from_webhook, sync_all, sync_page, sync_page_payload


configure_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)
app = FastAPI(title="notion-gcal-sync")


@app.get("/")
def healthcheck() -> dict[str, Any]:
    return {"ok": True, "service": "notion-gcal-sync"}


@app.post("/sync-page/{page_id}")
def sync_single_page(page_id: str) -> dict[str, Any]:
    result = sync_page(page_id)
    return result.model_dump()


@app.post("/sync-all")
def sync_everything() -> dict[str, Any]:
    result = sync_all()
    return result.model_dump(exclude={"results"})


@app.post("/notion-webhook")
async def notion_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    raw_body = await request.body()
    payload = json.loads(raw_body.decode("utf-8") or "{}")

    if payload.get("verification_token"):
        logger.info("Received Notion webhook verification token.")
        return {"ok": True, "message": "verification token received"}

    settings = get_settings()
    signature = request.headers.get("X-Notion-Signature")
    if settings.notion_webhook_secret:
        if not signature:
            raise HTTPException(status_code=401, detail="Missing Notion signature header.")
        _verify_notion_signature(raw_body, signature, settings.notion_webhook_secret)

    page_payloads = extract_page_payloads_from_webhook(payload)
    page_ids = sorted(page_payloads) or sorted(extract_page_ids_from_webhook(payload))
    logger.info("Received Notion webhook.", extra={"page_ids_count": len(page_ids), "top_level_keys": sorted(payload.keys())})

    if page_payloads:
        for page_payload in page_payloads.values():
            background_tasks.add_task(_background_sync_page_payload, page_payload)
        return {"ok": True, "queued_pages": len(page_payloads)}

    if page_ids:
        for page_id in page_ids:
            background_tasks.add_task(_background_sync_page, page_id)
        return {"ok": True, "queued_pages": len(page_ids)}

    background_tasks.add_task(_background_sync_all)
    return {"ok": True, "queued_sync_all": True}


def _verify_notion_signature(raw_body: bytes, provided_signature: str, secret: str) -> None:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    if not hmac.compare_digest(expected, provided_signature):
        raise HTTPException(status_code=401, detail="Invalid Notion signature.")


def _background_sync_page(page_id: str) -> None:
    try:
        sync_page(page_id)
    except Exception:
        logger.exception("Background sync_page failed.", extra={"page_id": page_id})


def _background_sync_page_payload(page: dict[str, Any]) -> None:
    page_id = page.get("id")
    try:
        sync_page_payload(page)
    except Exception:
        logger.exception("Background sync_page_payload failed.", extra={"page_id": page_id})


def _background_sync_all() -> None:
    try:
        sync_all()
    except Exception:
        logger.exception("Background sync_all failed.")
