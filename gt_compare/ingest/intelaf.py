"""Enumerador Intelaf: la API de búsqueda de su frontend, paginada de a 100.

La API exige un término de búsqueda (o un área del menú). El término "-"
coincide con casi todo el catálogo porque los códigos llevan guion; "o"
recoge la mayoría de los códigos sin guion. Medido: "-" = 3890 productos,
"o" agrega 9. Los totales por área coinciden con lo que trae "-", así que
recorrer las ~270 áreas costaría ~7x más peticiones sin ganar cobertura.
"""

from __future__ import annotations

import html as html_lib
import logging
from typing import AsyncIterator, Optional
from urllib.parse import quote

from ..scraper import _INTELAF_ENDPOINT, _intelaf_cash_price, _intelaf_list_price, _intelaf_price
from ..stores import Store
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord

logger = logging.getLogger("gt_compare.ingest")

QUERIES = ("-", "o")
PAGE_SIZE = 100          # máximo que acepta la API
MAX_PAGES_PER_QUERY = 80  # tope de seguridad (hoy ~39 páginas)


def _payload(query: str, page: int) -> dict:
    return {
        "PrecioMenor": 0,
        "PrecioMayor": 0,
        "Marcas": [],
        "SucursalesCodigo": [],
        "Orden": "default",
        "CantidadMaxima": PAGE_SIZE,
        "Query": query,
        "Categorias": [],
        "Pagina": page,
        "Acendente": True,
        "Instruccion": {"nombre": "busqueda", "valor": ""},
        "NumeroRecienIngreso": 0,
    }


def _stock(item: dict) -> int:
    # misma regla que scraper._parse_intelaf
    stock = sum(float(x.get("Existencia") or 0) for x in item.get("Existencia") or [])
    if item.get("EnBodega"):
        stock += 1
    if item.get("EnTransito"):
        stock += 1
    return int(stock)


def category_names(payload: dict) -> dict[str, str]:
    """Código de área -> descripción, tomado de las facetas de la respuesta."""
    data = payload.get("Response", payload) or {}
    out: dict[str, str] = {}
    for c in data.get("Categorias") or []:
        code, desc = c.get("Codigo"), c.get("Descripcion")
        if code and desc:
            out[code] = desc.strip()
    return out


def parse_page(
    store: Store, payload: dict, cat_names: Optional[dict[str, str]] = None
) -> tuple[list[ProductRecord], int]:
    """Registros con precio y el total declarado por la API."""
    data = payload.get("Response", payload) or {}
    cat_names = cat_names or {}
    out: list[ProductRecord] = []
    for item in data.get("Productos") or []:
        code = (item.get("Codigo") or "").strip()
        price = _intelaf_price(item)
        if not code or not price:
            continue
        list_price = _intelaf_list_price(item)
        cash_price = _intelaf_cash_price(item)
        promo = None
        if (list_price or cash_price) and item.get("DescripcionDescuento"):
            promo = item["DescripcionDescuento"].strip()
            until = item.get("FechaCaducaDescuento") or ""
            if until and not until.endswith("/0001"):  # 01/01/0001 = sin vencimiento
                promo += f" (hasta {until})"
        area = item.get("AreaFuncional") or None
        out.append(
            ProductRecord(
                store_key=store.key,
                store_sku=code,
                url=f"https://{store.domain}/producto/{quote(code, safe='')}",
                name=html_lib.unescape(item.get("Descripcion") or code).strip(),
                price=price,
                list_price=list_price,
                cash_price=cash_price,
                available=_stock(item),
                brand=(item.get("Marca") or "").strip() or None,
                image=item.get("Imagen") or None,
                store_category=cat_names.get(area, area) if area else None,
                promo_text=promo,
                first_sku=True,
                extra={"area": area} if area else {},
            )
        )
    return out, int(data.get("CantidadProductos") or 0)


async def _fetch(client: PoliteClient, store: Store, query: str, page: int) -> Optional[dict]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": f"https://{store.domain}",
        "Referer": f"https://{store.domain}/",
    }
    resp = await client.post(_INTELAF_ENDPOINT, json=_payload(query, page), headers=headers)
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    state = payload.get("state") or {}
    if state.get("Code") not in (None, 200):
        logger.warning("intelaf %r p%s: %s", query, page, state.get("Message"))
        return None
    return payload


async def enumerate_intelaf(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
) -> AsyncIterator[ProductRecord]:
    seen: set[str] = set()
    cat_names: dict[str, str] = {}
    declared = 0
    done: list[str] = []

    def note() -> str:
        terms = ", ".join(repr(q) for q in done) or "ninguna"
        return (f"casi completo por búsqueda amplia ({terms}): "
                f"{declared} declarados, {len(seen)} SKUs únicos")

    try:
        for query in QUERIES:
            page = 1
            while page <= MAX_PAGES_PER_QUERY:
                payload = await _fetch(client, store, query, page)
                stats.pages += 1
                if payload is None:
                    stats.errors += 1
                    break
                if page == 1:
                    cat_names.update(category_names(payload))
                recs, total = parse_page(store, payload, cat_names)
                declared = max(declared, total)
                for rec in recs:
                    if rec.store_sku in seen:
                        continue
                    seen.add(rec.store_sku)
                    stats.records += 1
                    yield rec
                    if limit and stats.records >= limit:
                        stats.coverage_note = f"parcial: límite de {limit} registros"
                        return
                data = payload.get("Response", payload) or {}
                if len(data.get("Productos") or []) < PAGE_SIZE:
                    break
                page += 1
            done.append(query)
            stats.coverage_note = note()
    except StoreBlocked:
        stats.errors += 1
        stats.coverage_note = note() + " (cortada por errores seguidos)"
        return
    stats.coverage_note = note()
