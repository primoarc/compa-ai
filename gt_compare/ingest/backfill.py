"""Relleno del historial desde los snapshots diarios del barrido viejo.

Los snapshots (`~/.gt-compare/snapshots/{tienda}-AAAAMMDD.json`) guardan solo el
primer SKU de cada producto y no traen id, así que cada producto entra con
`store_sku = "url:" + url` y `sku_level = 0`. La primera corrida de la ingesta
nueva hace que el primer SKU real adopte esa fila (ver `products.upsert_products`).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from ..db import Database
from ..history import URL_SKU_PREFIX, Observation, apply_observations, now_iso
from ..products import plausible_price, upsert_products
from ..vtex import _normalize_price
from .types import ProductRecord

logger = logging.getLogger("gt_compare.ingest")

SNAPSHOT_DIR = Path.home() / ".gt-compare" / "snapshots"
_NAME = re.compile(r"^([a-z0-9]+)-(\d{4})(\d{2})(\d{2})\.json$")


def snapshot_files(directory: Path = SNAPSHOT_DIR) -> list[tuple[str, str, Path]]:
    """(día ISO, tienda, ruta) en orden cronológico."""
    out = []
    for path in directory.glob("*.json"):
        m = _NAME.match(path.name)
        if m:
            out.append((f"{m[2]}-{m[3]}-{m[4]}", m[1], path))
    return sorted(out)


def records_from_snapshot(store_key: str, rows: list[dict]) -> list[ProductRecord]:
    out: dict[str, ProductRecord] = {}
    for row in rows:
        url = row.get("url")
        price = _normalize_price(row.get("price"))
        if not url or not plausible_price(price):
            continue
        out[url] = ProductRecord(
            store_key=store_key,
            store_sku=URL_SKU_PREFIX + url,
            url=url,
            name=(row.get("name") or "").strip(),
            price=price,
            list_price=_normalize_price(row.get("list_price")),
            available=1 if (row.get("available") or 0) > 0 else 0,
        )
    return list(out.values())


def backfill(db: Database, directory: Path = SNAPSHOT_DIR, *, only: Optional[list[str]] = None) -> dict:
    """Carga todos los snapshots. Es idempotente: días ya cargados se saltan."""
    done = {
        (r["store_key"], r["notes"])
        for r in db.query("SELECT store_key, notes FROM runs WHERE kind='backfill' AND status='ok'")
    }
    totals: dict[str, int] = {}
    for day, store_key, path in snapshot_files(directory):
        if only and store_key not in only:
            continue
        if (store_key, path.name) in done:
            continue
        try:
            rows = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("snapshot ilegible %s: %s", path.name, exc)
            continue
        records = records_from_snapshot(store_key, rows)
        run_id = db.execute(
            "INSERT INTO runs (store_key, kind, started_at, status, notes) VALUES (?,?,?,?,?)",
            (store_key, "backfill", now_iso(), "running", path.name),
        )
        changed = 0
        for i in range(0, len(records), 1000):
            chunk = records[i : i + 1000]
            ids = upsert_products(db, chunk, day)
            obs = [Observation(ids[r.store_sku], r.price, r.list_price, r.available)
                   for r in chunk if r.store_sku in ids]
            changed += apply_observations(db, day, obs, run_id)
        db.execute(
            "UPDATE runs SET finished_at=?, status='ok', seen=?, changed=? WHERE id=?",
            (now_iso(), len(records), changed, run_id),
        )
        totals[store_key] = totals.get(store_key, 0) + 1
        logger.info("relleno %s %s: %s productos, %s cambios", store_key, day, len(records), changed)
    # Solo el primer SKU del producto: el detector no los compara entre sí.
    db.execute("UPDATE products SET sku_level=0 WHERE store_sku LIKE ?", (URL_SKU_PREFIX + "%",))
    return totals
