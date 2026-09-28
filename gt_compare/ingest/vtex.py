"""Enumerador VTEX: catálogo completo por categoría raíz y rango de precio.

Reutiliza la división en cortes de `sweep` (probada con cobertura del 100% en
Siman) y lee cada SKU con EAN, marca, categoría y textos de promoción.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from .. import sweep
from ..stores import Store
from ..vtex import _normalize_price
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord

logger = logging.getLogger("gt_compare.ingest")


def _teaser_names(offer: dict) -> list[str]:
    names: list[str] = []
    for key in ("Teasers", "PromotionTeasers", "DiscountHighLight"):
        for t in offer.get(key) or []:
            if not isinstance(t, dict):
                continue
            name = t.get("<Name>k__BackingField") or t.get("Name") or t.get("name")
            if name and name not in names:
                names.append(str(name))
    return names


def records_from_product(store: Store, raw: dict) -> list[ProductRecord]:
    """Un registro por SKU con precio."""
    out: list[ProductRecord] = []
    items = raw.get("items") or []
    categories = raw.get("categories") or []
    brand = raw.get("brand") or None
    link = raw.get("link") or ""
    for idx, item in enumerate(items):
        sellers = item.get("sellers") or []
        offer = (sellers[0].get("commertialOffer") if sellers else None) or {}
        price = _normalize_price(offer.get("Price"))
        if not price:
            continue
        images = item.get("images") or []
        promos = _teaser_names(offer)
        out.append(
            ProductRecord(
                store_key=store.key,
                store_sku=str(item.get("itemId") or f"{raw.get('productId')}-{idx}"),
                url=link,
                name=(item.get("nameComplete") or raw.get("productName") or "").strip(),
                price=price,
                list_price=_normalize_price(offer.get("ListPrice")),
                available=1 if (offer.get("AvailableQuantity") or 0) > 0 else 0,
                brand=brand,
                ean=item.get("ean") or None,
                image=(images[0] or {}).get("imageUrl") if images else None,
                store_category=categories[0] if categories else None,
                promo_text=" | ".join(promos) or None,
                first_sku=idx == 0,
                extra={"product_id": raw.get("productId")},
            )
        )
    return out


async def _pages(client: PoliteClient, store: Store, fqs: list[str], stats: EnumerationStats):
    q = "&".join(f"fq={f}" for f in fqs)
    frm = 0
    while frm < sweep.MAX_OFFSET:
        url = (
            f"https://{store.domain}/api/catalog_system/pub/products/search"
            f"?{q}&_from={frm}&_to={frm + sweep.PAGE - 1}"
        )
        resp = await client.get(url)
        stats.pages += 1
        if resp.status_code not in (200, 206):
            stats.errors += 1
            return
        try:
            batch = resp.json()
        except ValueError:
            stats.errors += 1
            return
        if not batch:
            return
        yield batch
        if len(batch) < sweep.PAGE:
            return
        frm += sweep.PAGE


SLICE_WORKERS = 3  # cortes en paralelo; el cliente igual limita concurrencia y ritmo


async def enumerate_vtex(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
) -> AsyncIterator[ProductRecord]:
    cortes = await sweep._slices(client, store)  # type: ignore[arg-type]
    declared = sum(c["total"] for c in cortes)
    stats.coverage_note = f"catálogo completo por categoría y precio: {len(cortes)} cortes, {declared} productos declarados"
    pending = iter(cortes)
    queue: asyncio.Queue = asyncio.Queue(maxsize=SLICE_WORKERS * 2)
    blocked = False

    async def worker() -> None:
        nonlocal blocked
        try:
            for corte in pending:
                async for batch in _pages(client, store, corte["fqs"], stats):
                    await queue.put(batch)
        except StoreBlocked:
            blocked = True
        finally:
            await queue.put(None)

    tasks = [asyncio.create_task(worker()) for _ in range(SLICE_WORKERS)]
    seen: set[str] = set()
    done = 0
    try:
        while done < len(tasks):
            batch = await queue.get()
            if batch is None:
                done += 1
                continue
            for raw in batch:
                for rec in records_from_product(store, raw):
                    if rec.store_sku in seen:
                        continue
                    seen.add(rec.store_sku)
                    stats.records += 1
                    yield rec
                    if limit and stats.records >= limit:
                        return
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if blocked:
        stats.errors += 1
        stats.coverage_note += " (cortada por errores seguidos)"
