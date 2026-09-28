"""Enumerador de Novex por Doofinder.

Una consulta vacía es `match_all` (todo el catálogo), pero Doofinder solo deja
paginar los primeros 1000 resultados. Se parte el catálogo en rangos de
`best_price` que quepan en esa ventana (bisección) y, si un solo precio tiene
más de 1000 productos, ese rango se parte por categoría. La primera página de
cada rango sirve también de sondeo, y se ordena por id para que la paginación
sea estable.
"""

from __future__ import annotations

import html as html_lib
import re
from typing import AsyncIterator, Optional
from urllib.parse import quote

from ..scraper import _NOVEX_DOOFINDER, _NOVEX_HASHID, _as_price
from ..stores import Store
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord


RPP = 100
WINDOW = 1000            # Doofinder: "The requested page is too high" pasado 1000
MIN_WIDTH = 0.02         # rango de precio más angosto que se sigue partiendo
EDGES = [0, 5, 10, 15, 20, 30, 50, 75, 100, 150, 250, 500, 1000, 2500, 10000]
MAX_PROBES = 600         # tope de seguridad de rangos visitados

_GTIN = re.compile(r"\d{8}|\d{12,14}")


def _gtin(raw) -> Optional[str]:
    s = str(raw or "").strip()
    return s if _GTIN.fullmatch(s) else None


def record_from_result(store: Store, item: dict) -> Optional[ProductRecord]:
    sku = str(item.get("id") or "").strip()
    price = _as_price(item.get("best_price") or item.get("sale_price") or item.get("price"))
    if not sku or not price:
        return None
    regular = _as_price(item.get("price"))
    cats = item.get("categories") or []
    ean = None
    for key in ("gtin", "ean", "ean13", "upc", "barcode"):
        ean = _gtin(item.get(key))
        if ean:
            break
    extra = {k: item[k] for k in ("mpn", "stock", "df_grouping_id") if item.get(k)}
    return ProductRecord(
        store_key=store.key,
        store_sku=sku,
        url=item.get("link") or f"https://{store.domain}/producto/{quote(sku, safe='')}",
        name=html_lib.unescape(str(item.get("title") or sku)).strip(),
        price=price,
        list_price=regular if regular and regular > price else None,
        available=1 if str(item.get("availability") or "").strip().lower() == "in stock" else 0,
        brand=(str(item["brand"]).strip() or None) if item.get("brand") else None,
        ean=ean,
        image=item.get("image_link") or None,
        store_category=str(cats[0]).strip() if cats else None,
        promo_text=(str(item["etag"]).strip() or None) if item.get("etag") else None,
        first_sku=True,
        extra=extra,
    )


def _params(lo: Optional[float], hi: Optional[float], category: Optional[str] = None) -> dict:
    p: dict = {}
    if lo is not None:
        p["filter[best_price][gte]"] = f"{lo:.2f}"
    if hi is not None:
        p["filter[best_price][lt]"] = f"{hi:.2f}"
    if category:
        p["filter[categories][]"] = category
    return p


def _categories(data: dict) -> list[str]:
    facet = (data.get("facets") or {}).get("categories") or {}
    return [b["key"] for b in (facet.get("terms") or {}).get("buckets") or [] if b.get("key")]


async def _search(client: PoliteClient, store: Store, stats: EnumerationStats,
                  filters: dict, *, rpp: int = RPP, page: int = 1) -> Optional[dict]:
    params = {"hashid": _NOVEX_HASHID, "query": "", "rpp": str(rpp), "page": str(page),
              "sort[0][id]": "asc", **filters}
    headers = {"Accept": "application/json", "Origin": f"https://{store.domain}",
               "Referer": f"https://{store.domain}/"}
    resp = await client.get(_NOVEX_DOOFINDER, params=params, headers=headers)
    stats.pages += 1
    if resp.status_code != 200:
        stats.errors += 1
        return None
    try:
        return resp.json()
    except ValueError:
        stats.errors += 1
        return None


async def _slices(client: PoliteClient, store: Store, stats: EnumerationStats, notes: list):
    """Genera (filtros, total, primera página) de cada corte de ≤1000 productos."""
    pending: list = [(EDGES[i], EDGES[i + 1] if i + 1 < len(EDGES) else None, None)
                     for i in range(len(EDGES))]
    probes = 0
    while pending:
        probes += 1
        if probes > MAX_PROBES:
            notes.append(f"tope de {MAX_PROBES} sondeos")
            return
        lo, hi, cat = pending.pop(0)
        filters = _params(lo, hi, cat)
        data = await _search(client, store, stats, filters)
        if data is None:
            notes.append(f"sin respuesta en {filters}")
            continue
        n = int(data.get("total_found") or 0)
        if n == 0:
            continue
        if n <= WINDOW or cat:
            if n > WINDOW:
                notes.append(f"Q{lo:.2f}/{cat}: {n}, solo 1000 accesibles")
            yield filters, n, data
        elif hi is None:
            pending[:0] = [(lo, lo * 4, None), (lo * 4, None, None)]
        elif hi - lo >= MIN_WIDTH:
            mid = round((lo + hi) / 2, 2)
            pending[:0] = [(lo, mid, None), (mid, hi, None)]
        else:
            # un solo precio con más de 1000 productos: partir por categoría
            pending[:0] = [(lo, hi, c) for c in _categories(data)]


async def enumerate_novex(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
) -> AsyncIterator[ProductRecord]:
    seen: set[str] = set()
    notes: list[str] = []
    declared = 0
    reached = cuts = 0
    try:
        head = await _search(client, store, stats, {}, rpp=1)
        declared = int((head or {}).get("total_found") or 0)
        stats.coverage_note = f"catálogo por rangos de precio, {declared} productos declarados"
        async for filters, n, first in _slices(client, store, stats, notes):
            cuts += 1
            reached += min(n, WINDOW)
            data: Optional[dict] = first
            page = 1
            while data is not None:
                results = data.get("results") or []
                for item in results:
                    rec = record_from_result(store, item)
                    if rec is None or rec.store_sku in seen:
                        continue
                    seen.add(rec.store_sku)
                    stats.records += 1
                    yield rec
                    if limit and stats.records >= limit:
                        stats.coverage_note += f" | parcial: límite de {limit}"
                        return
                if len(results) < RPP or page * RPP >= min(n, WINDOW):
                    break
                page += 1
                data = await _search(client, store, stats, filters, page=page)
                if data is None:
                    notes.append(f"falló página {page} de {filters}")
        # los cortes cuentan documentos; el total vacío cuenta productos agrupados
        stats.coverage_note = (
            f"catálogo por rangos de precio: {cuts} cortes con {reached} resultados "
            f"alcanzables; Doofinder declara {declared} productos"
        )
        if notes:
            stats.coverage_note += " | " + "; ".join(notes)[:600]
    except StoreBlocked:
        stats.errors += 1
        stats.coverage_note += " (cortada por errores seguidos)"
