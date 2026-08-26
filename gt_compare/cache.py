"""Cache local en disco para no martillar las APIs en búsquedas repetidas.

Cada entrada es un archivo JSON con timestamp en ~/.gt-compare/cache/.
TTL configurable (default 30 min).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(os.getenv(
    "GT_COMPARE_CACHE_DIR",
    "/tmp/gt-compare-cache" if os.getenv("VERCEL") else Path.home() / ".gt-compare" / "cache",
))


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


def get_stale(key: str, max_age_seconds: int) -> tuple:
    """Devuelve (data, edad_en_segundos) aunque la entrada ya haya expirado.

    Se usa como red de seguridad cuando una tienda falla (p. ej. Kemik nos
    devuelve 429 desde las IPs de Vercel): es preferible mostrar el último
    precio conocido y decir de cuándo es, a que la tienda desaparezca del
    comparador. Por encima de `max_age_seconds` se descarta: un precio viejo
    deja de ser información y pasa a ser desinformación.
    """
    path = _key_to_path(key)
    if not path.exists():
        return (None, 0)
    try:
        with path.open(encoding="utf-8") as fh:
            blob = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return (None, 0)
    age = time.time() - blob.get("ts", 0)
    if age > max_age_seconds:
        return (None, 0)
    return (blob.get("data"), int(age))


def set(key: str, data: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _key_to_path(key)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "key": key, "data": data}, fh, ensure_ascii=False)
        tmp.replace(path)
    except OSError:
        return
