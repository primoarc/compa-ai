"""Barrido del catálogo completo de las tiendas VTEX.

Buscar por palabra clave devuelve 24 productos por tienda. Los catálogos reales
tienen decenas de miles:

    Siman     22,648      Walmart   33,636      Cemaco    41,340

Así que la cacería por búsquedas ve una milésima parte. Los rastreadores serios
(Keepa, Camelcamelcamel) no buscan: enumeran el catálogo entero y comparan cada
producto contra su propio historial. Esto es el primer paso de eso — la
enumeración. El historial necesita almacenamiento persistente.

VTEX lo permite con la misma API pública que ya usamos: se recorre el árbol de
categorías y se pagina dentro de cada una. El offset por categoría tiene tope,
así que las categorías grandes se abren por sus hijas.

Se va despacio a propósito: son miles de peticiones a tiendas que no nos deben
nada, y ya nos ganamos un 429 de Kemik por ir con prisa.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from .stores import Store
from .vtex import HEADERS, Product, _normalize_price

logger = logging.getLogger("gt_compare")

PAGE = 50
# VTEX corta la paginación pasado este offset; más allá hay que bajar a las
# subcategorías en vez de seguir pidiendo páginas.
MAX_OFFSET = 2500
# Peticiones simultáneas por tienda. Bajo a propósito.
CONCURRENCY = 4
# Pausa entre páginas de una misma categoría.
DELAY = 0.15


@dataclass
class SweepStats:
    categorias: int = 0
    paginas: int = 0
    productos: int = 0
    errores: int = 0


async def _category_tree(client: httpx.AsyncClient, store: Store, depth: int = 3) -> list:
    url = f"https://{store.domain}/api/catalog_system/pub/category/tree/{depth}"
    r = await client.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


async def _total_en_categoria(client: httpx.AsyncClient, store: Store, cid) -> int:
    """Cuántos productos declara la categoría (viene en el header 'resources')."""
    url = (
        f"https://{store.domain}/api/catalog_system/pub/products/search"
        f"?fq=C:{cid}&_from=0&_to=0"
    )
    try:
        r = await client.get(url, headers=HEADERS, timeout=30)
        res = r.headers.get("resources") or ""
        return int(res.split("/")[-1]) if "/" in res else 0
    except Exception:  # noqa: BLE001
        return 0


async def _categories(client: httpx.AsyncClient, store: Store, depth: int = 3) -> list:
    """Categorías a recorrer, sin solaparse.

    Se toma la categoría más alta que quepa entera bajo el tope de paginación;
    solo si no cabe se baja a sus hijas. Recorrer padre e hijas descargaría lo
    mismo dos veces, que además de lento es una descortesía con la tienda.
    """
    salida: list = []

    async def visitar(nodo) -> None:
        cid = nodo.get("id")
        if not cid:
            return
        total = await _total_en_categoria(client, store, cid)
        hijos = nodo.get("children") or []
        if total and total <= MAX_OFFSET:
            salida.append({"id": cid, "name": nodo.get("name", ""), "total": total})
            return
        if hijos:
            for h in hijos:
                await visitar(h)
        elif total:
            # Sin hijas y por encima del tope: se recorre lo que la API permita.
            salida.append({"id": cid, "name": nodo.get("name", ""), "total": total})

    for raiz in await _category_tree(client, store, depth):
        await visitar(raiz)
    return salida


def _to_product(store: Store, raw: dict) -> Optional[Product]:
    try:
        sku = (raw.get("items") or [{}])[0]
        seller = (sku.get("sellers") or [{}])[0]
        offer = seller.get("commertialOffer") or {}
        precio = _normalize_price(offer.get("Price"))
        if not precio:
            return None
        imgs = sku.get("images") or [{}]
        return Product(
            store_key=store.key,
            store_name=store.name,
            name=raw.get("productName", "—"),
            price=precio,
            available=int(offer.get("AvailableQuantity", 0) or 0),
            url=raw.get("link", ""),
            image=(imgs[0] or {}).get("imageUrl"),
            list_price=_normalize_price(offer.get("ListPrice")),
        )
    except (IndexError, KeyError, TypeError, ValueError):
        return None


async def _sweep_category(
    client: httpx.AsyncClient,
    store: Store,
    cat: dict,
    sem: asyncio.Semaphore,
    stats: SweepStats,
    vistos: dict,
) -> None:
    frm = 0
    while frm < MAX_OFFSET:
        url = (
            f"https://{store.domain}/api/catalog_system/pub/products/search"
            f"?fq=C:{cat['id']}&_from={frm}&_to={frm + PAGE - 1}"
        )
        async with sem:
            try:
                r = await client.get(url, headers=HEADERS, timeout=30)
            except Exception:  # noqa: BLE001
                stats.errores += 1
                return
        stats.paginas += 1
        if r.status_code not in (200, 206):
            stats.errores += 1
            return
        try:
            lote = r.json()
        except ValueError:
            stats.errores += 1
            return
        if not lote:
            return
        for raw in lote:
            p = _to_product(store, raw)
            if p and p.url not in vistos:
                vistos[p.url] = p
                stats.productos += 1
        if len(lote) < PAGE:
            return
        frm += PAGE
        await asyncio.sleep(DELAY)


async def sweep_store(store: Store, *, max_categorias: int = 0) -> tuple:
    """Recorre el catálogo de una tienda VTEX. Devuelve (productos, stats)."""
    stats = SweepStats()
    vistos: dict = {}
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        cats = await _categories(client, store)
        if max_categorias:
            cats = cats[:max_categorias]
        stats.categorias = len(cats)
        await asyncio.gather(
            *(_sweep_category(client, store, c, sem, stats, vistos) for c in cats)
        )
    return list(vistos.values()), stats


def anomalias_por_lista(
    productos: list,
    *,
    min_descuento: float = 0.70,
    min_referencia: float = 800.0,
) -> list:
    """Productos muy por debajo del precio de lista de su propia tienda."""
    salida = []
    for p in productos:
        lp = p.list_price
        if not lp or not p.price or lp <= p.price or lp < min_referencia:
            continue
        if p.available <= 0:
            continue
        desc = 1.0 - (p.price / lp)
        if desc < min_descuento:
            continue
        salida.append((desc, p))
    salida.sort(key=lambda x: -x[0])
    return salida
