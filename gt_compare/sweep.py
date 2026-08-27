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
from urllib.parse import urlparse

from .stores import Store
from .vtex import HEADERS, Product, _normalize_price

logger = logging.getLogger("gt_compare")

PAGE = 50
# Cortes de precio para partir categorías grandes. Densos abajo porque ahí se
# concentra el catálogo (ropa, belleza) y ralos arriba.
PRICE_EDGES = [
    0, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 650, 800, 1000,
    1300, 1700, 2200, 3000, 4000, 6000, 10000, 20000, 1_000_000,
]
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


async def _total(client: httpx.AsyncClient, store: Store, fqs: list) -> int:
    """Productos que declara una combinación de filtros (header 'resources')."""
    q = "&".join(f"fq={f}" for f in fqs)
    url = (
        f"https://{store.domain}/api/catalog_system/pub/products/search"
        f"?{q}&_from=0&_to=0"
    )
    try:
        r = await client.get(url, headers=HEADERS, timeout=30)
        res = r.headers.get("resources") or ""
        return int(res.split("/")[-1]) if "/" in res else 0
    except Exception:  # noqa: BLE001
        return 0


async def _buckets(
    client: httpx.AsyncClient,
    store: Store,
    cat_id,
    lo: float,
    hi: float,
    total: int,
    salida: list,
    profundidad: int = 0,
) -> None:
    """Parte una categoría en rangos de precio hasta que quepan bajo el tope.

    VTEX corta la paginación pasados ~2500 productos por consulta, y las rutas
    de subcategoría no filtran de forma fiable (devuelven el total del padre, o
    el catálogo entero si la ruta no es navegable). Partir por precio sí es
    exacto: la categoría Moda, con 12,990 productos, se reparte en rangos que
    suman 12,981.
    """
    fqs = [f"C:{cat_id}", f"P:[{lo:g} TO {hi:g}]"]
    if total <= MAX_OFFSET or profundidad >= 8 or hi - lo < 2:
        if total:
            salida.append({"fqs": fqs, "total": total})
        return
    medio = round((lo + hi) / 2, 2)
    for a, b in ((lo, medio), (medio, hi)):
        t = await _total(client, store, [f"C:{cat_id}", f"P:[{a:g} TO {b:g}]"])
        if t:
            await _buckets(client, store, cat_id, a, b, t, salida, profundidad + 1)


async def _slices(client: httpx.AsyncClient, store: Store) -> list:
    """Consultas que, juntas, cubren el catálogo completo.

    Solo se usan categorías raíz: son las únicas cuyo filtro por id responde
    correctamente en VTEX. En Siman suman 22,655 contra los 22,648 declarados,
    o sea el catálogo entero.
    """
    salida: list = []
    for raiz in await _category_tree(client, store, 1):
        cid = raiz.get("id")
        if not cid:
            continue
        total = await _total(client, store, [f"C:{cid}"])
        if not total:
            continue
        if total <= MAX_OFFSET:
            salida.append({"fqs": [f"C:{cid}"], "total": total})
            continue
        # Bisecar desde [0, 1000000] gasta la recursión partiendo rangos
        # vacíos: casi todo el catálogo vive debajo de Q3,000. Se arranca con
        # cortes de precio realistas y solo se bisecta dentro del que no quepa.
        for lo, hi in zip(PRICE_EDGES, PRICE_EDGES[1:]):
            t = await _total(client, store, [f"C:{cid}", f"P:[{lo:g} TO {hi:g}]"])
            if t:
                await _buckets(client, store, cid, lo, hi, t, salida)
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


async def _sweep_slice(
    client: httpx.AsyncClient,
    store: Store,
    corte: dict,
    sem: asyncio.Semaphore,
    stats: SweepStats,
    vistos: dict,
) -> None:
    q = "&".join(f"fq={f}" for f in corte["fqs"])
    frm = 0
    while frm < MAX_OFFSET:
        url = (
            f"https://{store.domain}/api/catalog_system/pub/products/search"
            f"?{q}&_from={frm}&_to={frm + PAGE - 1}"
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
            if p and p.url and p.url not in vistos:
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
        cortes = await _slices(client, store)
        if max_categorias:
            cortes = cortes[:max_categorias]
        stats.categorias = len(cortes)
        await asyncio.gather(
            *(_sweep_slice(client, store, c, sem, stats, vistos) for c in cortes)
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
