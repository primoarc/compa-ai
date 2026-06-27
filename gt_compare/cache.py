"""Cache local en disco para no martillar las APIs en búsquedas repetidas.

Cada entrada es un archivo JSON con timestamp en ~/.gt-compare/cache/.
TTL configurable (default 30 min).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path.home() / ".gt-compare" / "cache"


def _key_to_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return CACHE_DIR / f"{digest}.json"


def make_key(store_key: str, query: str) -> str:
    return f"{store_key}::{query.strip().lower()}"


def get(key: str, ttl_seconds: int) -> Any | None:
    """Devuelve el payload cacheado si existe y no expiró, si no None."""
    path = _key_to_path(key)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            blob = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - blob.get("ts", 0) > ttl_seconds:
        return None
    return blob.get("data")


def set(key: str, data: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _key_to_path(key)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"ts": time.time(), "key": key, "data": data}, fh, ensure_ascii=False)
    tmp.replace(path)
