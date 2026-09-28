"""Enumerador PriceSmart Guatemala sobre Bloomreach Discovery (dxpapi).

Recorrido: primero cada categoría raíz (search_type=category) para anotar la
categoría, luego un barrido `q=*` que recoge los productos sin categoría raíz.
Todo ordenado por pid para que la paginación sea estable. Solo se emiten
productos con precio del club GT por defecto (price_GT_6303).
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator, Optional

from ..products import normalize_ean
from ..scraper import (
    _PS_ACCOUNT, _PS_AUTH_KEY, _PS_DOMAIN_KEY, _PS_ENDPOINT, _PS_FL, _PS_INV_F,
    _PS_ORIG_F, _PS_VIEW_ID, _PS_CLUB, _ps_first_scalar, _ps_price, _ps_variant,
)
from ..stores import Store
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord

logger = logging.getLogger("gt_compare.ingest")

ROWS = 200
MAX_START = 10000  # tope de seguridad por consulta
_AVAIL_F = f"availability_{_PS_VIEW_ID}_{_PS_CLUB}"
FL = f"{_PS_FL},skuid,{_AVAIL_F}"
# skuid de variante: "{pid}-{GTIN}"
_RE_VARIANT_GTIN = re.compile(r"^\d+-(\d{8,14})$")


def _params(search_type: str, q: str, start: int, rows: int = ROWS) -> dict:
    return {
        "account_id": _PS_ACCOUNT, "auth_key": _PS_AUTH_KEY,
        "domain_key": _PS_DOMAIN_KEY, "view_id": _PS_VIEW_ID,
        "request_type": "search", "search_type": search_type,
        "q": q, "fl": FL, "rows": str(rows), "start": str(start),
        "sort": "pid asc", "request_id": "1", "_br_uid_2": "uid%3D1",
        "url": "https://www.pricesmart.com/es-gt",
    }


def _gtins(doc: dict) -> list[str]:
    """GTIN válidos que aparecen como sufijo de los skuid de variantes."""
    out: list[str] = []
    skus = [doc.get("master_sku")] + [v.get("skuid") for v in doc.get("variants") or []]
    for sku in skus:
        m = _RE_VARIANT_GTIN.match(str(sku or ""))
        if not m:
            continue
        code = m.group(1)
        if len(code) == 11:
            code = "0" + code  # UPC-A sin el cero inicial
        norm = normalize_ean(code)
        if norm and norm not in out:
            out.append(norm)
    return out


def _list_price(doc: dict, price: float) -> Optional[float]:
    # Viene en quetzales como texto (no en centavos), a veces solo en la variante.
    raw = doc.get(_PS_ORIG_F)
    if raw is None:
        raw = _ps_first_scalar(_ps_variant(doc).get(_PS_ORIG_F))
    if raw is None:
        return None
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    return value if value > price else None


def _flag(doc: dict, field: str) -> str:
    raw = doc.get(field)
    if raw is None:
        raw = _ps_first_scalar(_ps_variant(doc).get(field))
    return str(raw or "").strip().lower()


# Cargos que Bloomreach lista como productos.
_NOT_PRODUCTS = {"service fee"}


def record_from_doc(store: Store, doc: dict, category: Optional[str] = None) -> Optional[ProductRecord]:
    """Un registro por producto; None si no tiene precio del club GT."""
    pid = doc.get("pid")
    price = _ps_price(doc)
    if not pid or not price:
        return None
    if str(doc.get("title") or "").strip().lower() in _NOT_PRODUCTS:
        return None
    slug = doc.get("slug") or pid
    sku = doc.get("master_sku") or pid
    out_of_stock = _flag(doc, _PS_INV_F) == "out of stock" or _flag(doc, _AVAIL_F) == "false"
    gtins = _gtins(doc)
    extra: dict = {"master_sku": doc.get("master_sku")}
    if len(gtins) > 1:
        extra["gtins"] = gtins  # tallas/colores: no hay un único EAN del producto
    return ProductRecord(
        store_key=store.key,
        store_sku=str(pid),
        url=f"https://{store.domain}/es-gt/producto/{slug}/{sku}",
        name=(doc.get("title") or "").strip(),
        price=price,
        list_price=_list_price(doc, price),
        available=0 if out_of_stock else 1,
        brand=doc.get("brand") or None,
        ean=gtins[0] if len(gtins) == 1 else None,
        image=doc.get("thumb_image") or None,
        store_category=category,
        first_sku=True,
        extra=extra,
    )


async def _get(client: PoliteClient, stats: EnumerationStats, params: dict) -> Optional[dict]:
    resp = await client.get(_PS_ENDPOINT, params=params)
    stats.pages += 1
    if resp.status_code != 200:
        stats.errors += 1
        logger.warning("pricesmart %s q=%s start=%s -> %s", params["search_type"],
                       params["q"], params["start"], resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        stats.errors += 1
        return None


async def _docs(client: PoliteClient, stats: EnumerationStats, search_type: str, q: str):
    """Pagina una consulta hasta numFound; devuelve los docs de cada página."""
    start = 0
    while start < MAX_START:
        payload = await _get(client, stats, _params(search_type, q, start))
        if payload is None:
            return
        response = payload.get("response") or {}
        docs = response.get("docs") or []
        total = int(response.get("numFound") or 0)
        yield docs
        start += ROWS
        if not docs or start >= total:
            return


def _root_categories(payload: dict) -> list[tuple[str, str]]:
    cats = ((payload.get("facet_counts") or {}).get("facet_fields") or {}).get("category") or []
    return [(c["cat_id"], c.get("cat_name") or c["cat_id"])
            for c in cats if c.get("cat_id") and not c.get("parent")]


async def enumerate_pricesmart(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
) -> AsyncIterator[ProductRecord]:
    seen: set[str] = set()
    sin_precio: set[str] = set()
    total = 0
    swept = False
    phase = "inicio"

    def note() -> str:
        return (f"Bloomreach view GT, club {_PS_CLUB}: {len(seen)}/{total} pid vistos "
                f"({len(sin_precio)} sin precio del club), {stats.records} registros; fase {phase}")

    try:
        head = await _get(client, stats, _params("keyword", "*", 0, rows=1))
        if head is None:
            stats.coverage_note = "sin respuesta de Bloomreach"
            return
        total = int((head.get("response") or {}).get("numFound") or 0)
        roots = _root_categories(head)
        queries: list[tuple[str, str, Optional[str]]] = [("category", cid, name) for cid, name in roots]
        # El barrido final recoge lo que no cuelga de ninguna categoría raíz.
        queries.append(("keyword", "*", None))
        for search_type, q, category in queries:
            phase = f"{search_type}:{q}"
            if search_type == "keyword":
                if len(seen) >= total:
                    break
                swept = True
            async for docs in _docs(client, stats, search_type, q):
                for doc in docs:
                    pid = str(doc.get("pid") or "")
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    rec = record_from_doc(store, doc, category)
                    if rec is None:
                        sin_precio.add(pid)
                        continue
                    stats.records += 1
                    yield rec
                    if limit and stats.records >= limit:
                        phase = "límite alcanzado"
                        stats.coverage_note = "parcial: " + note()
                        return
        phase = f"{len(roots)} categorías raíz" + (" + barrido q=*" if swept else "")
        full = len(seen) >= total
        stats.coverage_note = ("catálogo completo: " if full else "parcial: ") + note()
    except StoreBlocked:
        stats.errors += 1
        stats.coverage_note = "parcial (cortada por errores seguidos): " + note()
