"""Búsqueda sobre el catálogo guardado, para tiendas cuyo buscador prohíbe robots.txt.

La Curacao, RadioShack, Steren y EPA prohíben sus páginas de búsqueda
(`/search/`, `/catalogsearch/`). Para ellas no se consulta la tienda en vivo:
se busca en el catálogo que la ingesta diaria recorre por categorías, que sí
están permitidas. El precio es el de la última corrida y se etiqueta con su
edad: "actualizado hace X" por producto.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

from . import relevance
from .db import Database, get_db
from .stores import Store
from .vtex import Product, StoreResult

# Tiendas que se buscan desde el catálogo y no en vivo.
CATALOG_STORES = {"curacao", "radioshack", "steren", "epa"}
MAX_RESULTS = 60
ENOUGH = 5

_WORD = re.compile(r"[a-z0-9]+")


def _fts_query(text: str) -> Optional[str]:
    """Todas las palabras de contenido, con prefijo ("tele" encuentra "televisor")."""
    words = [w for w in _WORD.findall(relevance.normalize(text)) if w not in relevance._STOPWORDS]
    return " AND ".join(f'"{w}"*' for w in words) if words else None


def _last_run(db: Database, store_key: str) -> Optional[datetime]:
    row = db.query_one(
        """SELECT MAX(finished_at) AS f FROM runs
           WHERE store_key=? AND kind='ingest' AND status IN ('ok','partial')""",
        (store_key,),
    )
    return datetime.fromisoformat(row["f"]) if row and row["f"] else None


def seen_age(seen_day: str, last_run: datetime, now: datetime) -> int:
    """Segundos desde que se vio un precio. Estas tiendas se marcan como vistas
    en cada corrida, así que un end_day igual al día de la última corrida es esa
    corrida; uno anterior (corrida parcial que no lo alcanzó) cuenta días enteros."""
    run_age = int((now - last_run).total_seconds())
    days = (last_run.date() - date.fromisoformat(seen_day)).days
    return run_age if days <= 0 else run_age + days * 86400


def search(store: Store, query: str, *, db: Optional[Database] = None, plan=None) -> StoreResult:
    db = db or get_db()
    if db is None:
        return StoreResult(store, [], ok=False, error="catálogo no disponible")
    last_run = _last_run(db, store.key)
    if last_run is None:
        return StoreResult(store, [], ok=False, error="catálogo sin corridas")
    now = datetime.now(timezone.utc)
    variants = list(plan.search_queries[:6]) if plan is not None and getattr(plan, "search_queries", None) \
        else relevance.search_queries(query, limit=4)
    seen: set[int] = set()
    products: list[Product] = []
    for variant in variants or [query]:
        match = _fts_query(variant)
        if not match:
            continue
        rows = db.query(
            """SELECT p.id, p.name, p.url, p.image, p.cur_price, p.cur_list_price, p.cur_available,
                      p.cur_cash_price,
                      (SELECT MAX(h.end_day) FROM price_history h WHERE h.product_id = p.id) AS seen_day
               FROM product_search JOIN products p ON p.id = product_search.rowid
               WHERE product_search MATCH ? AND p.store_key = ? AND p.sku_level = 1
                 AND p.cur_price IS NOT NULL
                 -- "+" evita el índice de end_day: se busca por producto, pocas filas
                 AND EXISTS (SELECT 1 FROM price_history h WHERE h.product_id = p.id
                             AND +h.end_day >= date('now', '-3 day'))
               ORDER BY product_search.rank LIMIT ?""",
            (match, store.key, MAX_RESULTS),
        )
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            products.append(Product(
                store_key=store.key, store_name=store.name, name=r["name"], price=r["cur_price"],
                available=1 if (r["cur_available"] is None or r["cur_available"] > 0) else 0,
                url=r["url"], image=r["image"], list_price=r["cur_list_price"],
                cash_price=r["cur_cash_price"], seen_age=seen_age(r["seen_day"], last_run, now),
            ))
        if sum(1 for p in products if relevance.is_relevant(query, p.name)) >= ENOUGH:
            break
    return StoreResult(store, products, ok=True)
