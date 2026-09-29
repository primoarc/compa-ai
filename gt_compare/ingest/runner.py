"""Corre la ingesta de una o varias tiendas y la guarda en el historial."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from ..catalog_search import CATALOG_STORES
from ..db import Database
from ..history import Observation, apply_observations, now_iso, today_utc
from ..products import plausible_price, upsert_products
from ..stores import Store, load_stores
from .http import PoliteClient
from .types import EnumerationStats, ProductRecord

logger = logging.getLogger("gt_compare.ingest")

BATCH = 1000

# Ritmo por tienda: concurrencia y pausa mínima entre peticiones (segundos).
# Kemik ya nos devolvió 429 con ráfagas cortas y 404 falsos con ritmo sostenido
# de 1,5 s: va de a una y más lento.
PACING = {
    "kemik": (1, 2.5),
    "intelaf": (2, 0.6),
    "pricesmart": (2, 0.5),
    "novex": (2, 0.5),
    "sears": (2, 0.5),
    # Desde IPs de GitHub respondieron 406 intermitentes con 0,3 s y con 1 s.
    "curacao": (1, 2.5),
    "radioshack": (1, 2.5),
}
DEFAULT_PACING = (3, 0.3)

# Días mínimos entre corridas. Novex busca con Doofinder, que probablemente le
# cobra cada búsqueda a la tienda: un recorrido completo son ~300.
CADENCE_DAYS = {"novex": 3}
VTEX_PACING = (4, 0.15)


def daily_marked(db: Database) -> frozenset[int]:
    """Productos que se marcan como vistos todos los días: los que están en
    /ofertas o en la cola del panel (último día de ofertas) y la oferta del día."""
    rows = db.query(
        """SELECT product_id FROM deals
           WHERE detected_on = (SELECT MAX(detected_on) FROM deals)
             AND status IN ('published', 'approved', 'pending')
           UNION
           SELECT d.product_id FROM daily_pick dp JOIN deals d ON d.id = dp.deal_id
           WHERE dp.day >= date('now', '-1 day')"""
    )
    return frozenset(r["product_id"] for r in rows)


def enumerator_for(store: Store) -> Optional[Callable]:
    from . import registry

    return registry.for_store(store)


@dataclass
class RunResult:
    store_key: str
    run_id: int
    status: str
    records: int
    changed: int
    pages: int
    errors: int
    seconds: float
    note: str


async def run_store(
    db: Database, store: Store, *, day: Optional[str] = None, limit: int = 0,
    enumerator: Optional[Callable] = None,
) -> RunResult:
    day = day or today_utc()
    enum = enumerator or enumerator_for(store)
    started = time.monotonic()
    run_id = db.execute(
        "INSERT INTO runs (store_key, kind, started_at, status) VALUES (?,?,?,?)",
        (store.key, "ingest", now_iso(), "running"),
    )
    if enum is None:
        db.execute(
            "UPDATE runs SET finished_at=?, status=?, notes=? WHERE id=?",
            (now_iso(), "skipped", "sin enumerador para esta tienda", run_id),
        )
        return RunResult(store.key, run_id, "skipped", 0, 0, 0, 0, 0.0, "sin enumerador")

    stats = EnumerationStats()
    concurrency, interval = (
        VTEX_PACING if store.kind == "vtex" else PACING.get(store.key, DEFAULT_PACING)
    )
    daily = daily_marked(db)
    # Sus resultados de búsqueda salen del catálogo con "actualizado hace X":
    # se marcan como vistos todos los días para que esa edad sea exacta.
    mark_all = store.key in CATALOG_STORES
    changed = 0
    batch: list[ProductRecord] = []
    status = "ok"

    def flush() -> None:
        nonlocal changed, batch
        if not batch:
            return
        ids = upsert_products(db, batch, day)
        obs = [
            Observation(ids[r.store_sku], r.price, r.list_price,
                        1 if r.available is None else int(r.available > 0), r.cash_price)
            for r in batch
            if r.store_sku in ids
        ]
        marked = daily | frozenset(o.product_id for o in obs) if mark_all else daily
        changed += apply_observations(db, day, obs, run_id, marked)
        batch = []

    try:
        async with PoliteClient(store.key, concurrency=concurrency, min_interval=interval) as client:
            async for rec in enum(store, client, stats, limit=limit):
                if not plausible_price(rec.price):
                    continue  # precio de relleno: el artículo no está a la venta
                batch.append(rec)
                if len(batch) >= BATCH:
                    flush()
            # Los enumeradores ya cuentan sus respuestas fallidas; el cliente
            # cuenta además las que agotaron reintentos. Sin sumar dos veces.
            stats.errors = max(stats.errors, client.failures)
        flush()
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingesta %s falló", store.key)
        flush()
        status = "failed" if stats.records == 0 else "partial"
        stats.coverage_note += f" | error: {type(exc).__name__}: {exc}"
    if status == "ok" and (stats.partial or "cortada" in stats.coverage_note
                           or stats.errors > max(5, stats.pages // 10)):
        status = "partial"

    seconds = time.monotonic() - started
    db.execute(
        """UPDATE runs SET finished_at=?, status=?, pages=?, seen=?, changed=?, errors=?, notes=?
           WHERE id=?""",
        (now_iso(), status, stats.pages, stats.records, changed, stats.errors,
         stats.coverage_note[:1000], run_id),
    )
    logger.info("%s: %s, %s registros, %s cambios, %s páginas, %s errores, %.0fs",
                store.key, status, stats.records, changed, stats.pages, stats.errors, seconds)
    return RunResult(store.key, run_id, status, stats.records, changed, stats.pages,
                     stats.errors, seconds, stats.coverage_note)


def _due(db: Database, store_key: str, day: str) -> bool:
    every = CADENCE_DAYS.get(store_key)
    if not every:
        return True
    row = db.query_one(
        "SELECT MAX(substr(started_at, 1, 10)) AS d FROM runs "
        "WHERE store_key=? AND kind='ingest' AND status IN ('ok','partial')",
        (store_key,),
    )
    if not row or not row["d"]:
        return True
    return (date.fromisoformat(day) - date.fromisoformat(row["d"])).days >= every


ABANDON_AFTER_HOURS = 6  # más que el timeout del workflow (5 h)


def close_abandoned(db: Database) -> int:
    """Corridas que quedaron en 'running' (cancelada, Mac apagada a mitad): se
    marcan 'abandoned' para que nadie las lea como vigentes."""
    return db.execute(
        """UPDATE runs SET status='abandoned', finished_at=?
           WHERE status='running' AND started_at < ?""",
        (now_iso(), (datetime.now(timezone.utc) - timedelta(hours=ABANDON_AFTER_HOURS))
         .replace(microsecond=0).isoformat()),
    )


async def run_all(db: Database, *, only: Optional[list[str]] = None, limit: int = 0,
                  day: Optional[str] = None, cadence: Optional[bool] = None) -> list[RunResult]:
    """Tiendas de a una: nada de esto es tan urgente como para pegarle a dos a la vez.

    `cadence`: respetar los días entre corridas (Novex cada 3). Por defecto se
    respeta salvo que `only` nombre tiendas a mano.
    """
    day = day or today_utc()
    respect = (not only) if cadence is None else cadence
    abandoned = close_abandoned(db)
    if abandoned:
        logger.info("%s corridas viejas quedaron en 'running': marcadas 'abandoned'", abandoned)
    results = []
    for store in load_stores():
        if only and store.key not in only:
            continue
        if respect and not _due(db, store.key, day):
            logger.info("%s: no le toca hoy", store.key)
            continue
        results.append(await run_store(db, store, day=day, limit=limit))
    return results
