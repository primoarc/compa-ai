"""Enumerador de Sears Guatemala (WooCommerce).

La Store API pública (`/wp-json/wc/store/products`) devuelve el catálogo
completo en páginas de 100 con el total en `X-WP-Total`. Si algún día la
cierran, se cae al listado HTML de la tienda con el parser de `scraper`.
"""

from __future__ import annotations

import html as html_lib
import re
from typing import AsyncIterator, Optional

from ..scraper import _WC_LINK, _parse_woocommerce
from ..stores import Store
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord


# La Store API tarda ~0,5 s por producto en el servidor (7-oct: 45-70 s por página
# de 100, ~25 s por página de 50): páginas de 50 con espera larga (runner.TIMEOUTS).
PER_PAGE = 50
MAX_PAGES = 200          # tope de seguridad (hoy son ~72)
MAX_HTML_PAGES = 400     # listado HTML: 12 por página (hoy ~227)

_GTIN = re.compile(r"\d{8}|\d{12,14}")
_LI_POST = re.compile(
    r'<li\b[^>]*class="[^"]*\bpost-(\d+)\b[^"]*"[^>]*>(.*?)</li>', re.I | re.S
)
_BRAND_ATTRS = {"marca", "brand", "fabricante"}
_EAN_ATTRS = {"ean", "gtin", "upc", "codigo de barras", "código de barras"}


def _gtin(raw) -> Optional[str]:
    s = str(raw or "").strip()
    return s if _GTIN.fullmatch(s) else None


def _money(raw, minor: int) -> Optional[float]:
    try:
        v = int(str(raw)) / (10 ** minor)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if v > 0 else None


def _attr(raw: dict, names: set) -> Optional[str]:
    for a in raw.get("attributes") or []:
        if str(a.get("name") or "").strip().lower() in names:
            terms = a.get("terms") or []
            if terms and terms[0].get("name"):
                return html_lib.unescape(str(terms[0]["name"])).strip()
    return None


def record_from_api(store: Store, raw: dict) -> Optional[ProductRecord]:
    """Un producto de la Store API. Los precios vienen en unidades menores."""
    prices = raw.get("prices") or {}
    minor = int(prices.get("currency_minor_unit") or 0)
    price = _money(prices.get("price"), minor)
    if not price:
        return None
    regular = _money(prices.get("regular_price"), minor)
    cats = raw.get("categories") or []
    images = raw.get("images") or []
    brands = raw.get("brands") or []
    brand = (brands[0].get("name") if brands and isinstance(brands[0], dict) else None) \
        or _attr(raw, _BRAND_ATTRS)
    return ProductRecord(
        store_key=store.key,
        store_sku=str(raw["id"]),
        url=raw.get("permalink") or "",
        name=html_lib.unescape(str(raw.get("name") or "")).strip(),
        price=price,
        list_price=regular if regular and regular > price else None,
        available=1 if raw.get("is_in_stock") else 0,
        brand=html_lib.unescape(brand) if brand else None,
        ean=_gtin(raw.get("sku")) or _gtin(_attr(raw, _EAN_ATTRS)),
        image=(images[0] or {}).get("src") if images else None,
        store_category=html_lib.unescape(cats[0]["name"]) if cats and cats[0].get("name") else None,
        first_sku=True,
        extra={"sku": raw.get("sku") or None, "type": raw.get("type")},
    )


def records_from_html(store: Store, page_html: str) -> list[ProductRecord]:
    """Listado HTML: id del post (mismo id que la API) + parser de `scraper`."""
    ids: dict[str, str] = {}
    for m in _LI_POST.finditer(page_html):
        link = _WC_LINK.search(m.group(2))
        if link:
            ids[link.group(1)] = m.group(1)
    out = []
    for p in _parse_woocommerce(store, page_html):
        if not p.price:
            continue
        out.append(ProductRecord(
            store_key=store.key,
            store_sku=ids.get(p.url) or p.url.rstrip("/").rsplit("/", 1)[-1],
            url=p.url,
            name=p.name,
            price=p.price,
            list_price=p.list_price,
            available=p.available,
            image=p.image,
        ))
    return out


async def _from_api(store, client, stats, seen, limit):
    """Genera registros de la Store API; devuelve sin nada si no está disponible."""
    base = f"https://{store.domain}/wp-json/wc/store/products"
    total_pages = None
    page = 1
    while page <= min(total_pages or MAX_PAGES, MAX_PAGES):
        resp = await client.get(base, params={"per_page": PER_PAGE, "page": page})
        stats.pages += 1
        if resp.status_code != 200:
            stats.errors += 1
            if page == 1:
                return
            stats.coverage_note += f" (cortada: página {page} respondió {resp.status_code})"
            return
        try:
            batch = resp.json()
        except ValueError:
            stats.errors += 1
            if page > 1:
                stats.coverage_note += f" (cortada: página {page} no es JSON)"
            return
        if not isinstance(batch, list) or not batch:
            return
        if total_pages is None:
            total_pages = int(resp.headers.get("x-wp-totalpages") or MAX_PAGES)
            declared = resp.headers.get("x-wp-total") or "?"
            stats.coverage_note = (
                f"catálogo completo por Store API: {declared} productos en {total_pages} páginas"
            )
        for raw in batch:
            rec = record_from_api(store, raw)
            if rec is None or rec.store_sku in seen:
                continue
            seen.add(rec.store_sku)
            stats.records += 1
            yield rec
            if limit and stats.records >= limit:
                stats.coverage_note += f" | parcial: límite de {limit}"
                return
        if len(batch) < PER_PAGE:
            return
        page += 1


async def _from_html(store, client, stats, seen, limit):
    stats.coverage_note = "listado HTML de la tienda (Store API no disponible)"
    for page in range(1, MAX_HTML_PAGES + 1):
        url = f"https://{store.domain}/page/{page}/?post_type=product"
        resp = await client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        stats.pages += 1
        if resp.status_code == 404:
            return
        if resp.status_code != 200:
            stats.errors += 1
            stats.coverage_note += f" (cortada: página {page} respondió {resp.status_code})"
            return
        recs = records_from_html(store, resp.text)
        if not recs:
            return
        for rec in recs:
            if rec.store_sku in seen:
                continue
            seen.add(rec.store_sku)
            stats.records += 1
            yield rec
            if limit and stats.records >= limit:
                return
    stats.coverage_note += f" (tope de {MAX_HTML_PAGES} páginas)"


async def enumerate_sears(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
) -> AsyncIterator[ProductRecord]:
    seen: set[str] = set()
    try:
        async for rec in _from_api(store, client, stats, seen, limit):
            yield rec
        if seen:
            return
        async for rec in _from_html(store, client, stats, seen, limit):
            yield rec
    except StoreBlocked:
        stats.errors += 1
        stats.coverage_note += " (cortada por errores seguidos)"
