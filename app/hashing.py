from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_sync_hash(task_payload: dict[str, Any]) -> str:
    serialized = json.dumps(task_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

