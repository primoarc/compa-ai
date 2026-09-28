"""Enumerador Kemik: árbol de categorías del HTML SSR (Next.js App Router).

Cada página de categoría (/{slug}?page=N) trae 40 productos dentro del payload
RSC (`self.__next_f.push`) con sku, precio normal y de oferta, stock, marca y
categoría, más un `categoryNavigator` con el conteo de la categoría y de sus
hijas. La paginación se corta en 25 páginas (1000 productos), así que se baja
por el árbol hasta que cada nodo entra en ese tope.

Kemik responde 429 con facilidad: el runner va de a una petición cada 1.5 s y
aquí hay un presupuesto de páginas por corrida. El orden de las categorías raíz
rota por día para que el recorte no caiga siempre sobre las mismas.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from typing import AsyncIterator, Optional

from ..scraper import _parse_kemik
from ..stores import Store
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord

PAGE = 40
PAGE_CAP = 25            # la página 26 da 404
# Bajo carga Kemik responde 404 (no 429) a páginas que existen: en la corrida del
# 28-sep, 144 de 600 páginas dieron 404 y al pedirlas sueltas daban 200 o 429.
# Un 404 se reintenta una vez después de una pausa.
RETRY_404_AFTER = 20.0
MAX_PAGES = 600          # presupuesto por corrida (~25 min al ritmo del runner)
CDN = "https://static.kemikcdn.com/"
IN_STOCK = {"IN_STOCK", "IN_ERP_STOCK"}

# Raíces vistas en el navegador de /search?query= (respaldo si falla).
ROOTS = (
    "tecnologia", "computadoras-accesorios", "celulares-accesorios", "audio",
    "television-video", "gaming", "hogar", "herramientas-mejoras-del-hogar",
    "instrumentos-musicales-audio-pro", "carros-motos", "juguetes-juegos",
    "deportes", "salud", "cuidado-personal-belleza", "mascotas", "libros",
    "utiles-escolares-oficina", "seguridad-vigilancia", "bebidas-alimentos",
    "ropa-accesorios", "musica-merchandising",
)
SKIP_ROOTS = {"uncategorized", "tarjetas-de-regalo", "promocionales-kemik"}

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_DEC = json.JSONDecoder()


def rsc_text(html: str) -> str:
    """Concatena los fragmentos del payload RSC (son literales de string JSON)."""
    parts = []
    for raw in _PUSH.findall(html):
        try:
            parts.append(json.loads('"' + raw + '"'))
        except ValueError:
            continue
    return "".join(parts)


def _objects(text: str, marker: str) -> list[dict]:
    out = []
    for m in re.finditer(re.escape(marker), text):
        try:
            obj = _DEC.raw_decode(text, m.start())[0]
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _ld_items(html: str) -> list[dict]:
    """Productos del ItemList schema.org (respaldo y fuente de la imagen)."""
    items = []
    for raw in _LD.findall(html):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "ItemList":
            for el in data.get("itemListElement") or []:
                item = (el or {}).get("item")
                if isinstance(item, dict) and item.get("sku"):
                    items.append(item)
    return items


def _num(v) -> Optional[float]:
    try:
        f = round(float(v), 2)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _name(v) -> Optional[str]:
    # RSC puede reemplazar objetos repetidos por referencias "$..."
    if isinstance(v, dict):
        return (v.get("name") or "").strip() or None
    return None


def navigator(html: str, text: Optional[str] = None) -> tuple[Optional[int], list[tuple[str, int]]]:
    """(conteo de la categoría actual, [(slug hija, conteo)])."""
    text = rsc_text(html) if text is None else text
    key = '"categoryNavigator":'
    i = text.find(key)
    if i < 0:
        return None, []
    try:
        nav = _DEC.raw_decode(text, i + len(key))[0]
    except ValueError:
        return None, []
    count = (nav.get("current") or {}).get("count")
    children = []
    for c in nav.get("children") or []:
        slug = ((c or {}).get("value") or {}).get("slug")
        if slug:
            children.append((slug, int(c.get("count") or 0)))
    return (int(count) if count is not None else None), children


def parse_products(store: Store, html: str, text: Optional[str] = None) -> list[ProductRecord]:
    text = rsc_text(html) if text is None else text
    ld = {str(i["sku"]): i for i in _ld_items(html)}
    base = f"https://{store.domain}/"
    out: list[ProductRecord] = []
    for o in _objects(text, '{"public_sku":'):
        sku, slug = o.get("public_sku"), o.get("slug")
        price = _num(o.get("price")) or _num(o.get("sale_price"))
        if not sku or not slug or not price or o.get("status", "PUBLISHED") != "PUBLISHED":
            continue
        regular = _num(o.get("regular_price"))
        ld_item = ld.get(str(sku)) or {}
        image = ld_item.get("image")
        if not image and isinstance(o.get("main_image"), dict) and o["main_image"].get("name"):
            image = CDN + o["main_image"]["name"]
        out.append(ProductRecord(
            store_key=store.key,
            store_sku=str(sku),
            url=base + slug,
            name=(o.get("title") or ld_item.get("name") or slug).strip(),
            price=price,
            list_price=regular if regular and regular > price else None,
            available=1 if o.get("stock_status") in IN_STOCK else 0,
            brand=_name(o.get("brand")) or _name(ld_item.get("brand")),
            image=image,
            store_category=_name(o.get("main_category")),
            promo_text="Oferta flash" if o.get("has_flash_sale") else None,
            first_sku=True,
            extra={"slug": slug, "international": bool(o.get("is_international"))},
        ))
    if out:
        return out
    # Respaldo 1: JSON-LD (sin precio normal ni categoría).
    for sku, item in ld.items():
        offer = item.get("offers") or {}
        price = _num(offer.get("price"))
        url = item.get("url") or ""
        if not price or not url:
            continue
        out.append(ProductRecord(
            store_key=store.key, store_sku=sku, url=url,
            name=(item.get("name") or "").strip(), price=price,
            available=1 if "InStock" in str(offer.get("availability")) else 0,
            brand=_name(item.get("brand")), image=item.get("image"),
            extra={"slug": url.rsplit("/", 1)[-1]},
        ))
    if out:
        return out
    # Respaldo 2: regex del scraper; sin sku, se usa el slug.
    for p in _parse_kemik(store, html):
        slug = p.url.rsplit("/", 1)[-1]
        out.append(ProductRecord(
            store_key=store.key, store_sku=slug, url=p.url, name=p.name,
            price=p.price, available=p.available, image=p.image,
            extra={"slug": slug},
        ))
    return out


def _url(store: Store, slug: str, page: int) -> str:
    path = f"https://{store.domain}/{slug}"
    return path if page <= 1 else f"{path}?page={page}"


def _rotate(roots: list[str], day: dt.date) -> list[str]:
    if not roots:
        return roots
    k = day.toordinal() % len(roots)
    return roots[k:] + roots[:k]


class _Budget(Exception):
    pass


async def enumerate_kemik(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0,
    max_pages: int = MAX_PAGES,
) -> AsyncIterator[ProductRecord]:
    seen: set[str] = set()
    visited: set[str] = set()
    declared = 0
    gaps = 0          # productos que el tope de 25 páginas deja fuera
    requests = 0

    async def get(url: str) -> Optional[str]:
        nonlocal requests
        if requests >= max_pages:
            raise _Budget
        requests += 1
        resp = await client.get(url)
        stats.pages += 1
        if resp.status_code == 404 and requests < max_pages:
            await client.pause(RETRY_404_AFTER)
            requests += 1
            resp = await client.get(url)
            stats.pages += 1
        if resp.status_code != 200:
            stats.errors += 1
            return None
        return resp.text

    def note(extra: str = "") -> str:
        parts = [f"categorías: {len(visited)} visitadas, {requests} páginas, "
                 f"{declared} productos declarados en raíces, {len(seen)} SKUs únicos"]
        if gaps:
            parts.append(f"~{gaps} fuera por el tope de {PAGE_CAP} páginas")
        if extra:
            parts.append(extra)
        return "; ".join(parts)

    def emit(recs: list[ProductRecord]) -> list[ProductRecord]:
        fresh = []
        for rec in recs:
            if rec.store_sku not in seen:
                seen.add(rec.store_sku)
                fresh.append(rec)
        return fresh

    # Raíces desde el navegador de la búsqueda vacía; si falla, la lista fija.
    roots: list[str] = []
    stack: list[tuple[str, bool]] = []
    try:
        html = await get(f"https://{store.domain}/search?query=")
        if html:
            _, kids = navigator(html)
            roots = [s for s, _ in kids if s not in SKIP_ROOTS]
        roots = _rotate(roots or list(ROOTS), dt.date.today())
        stack = [(s, True) for s in reversed(roots)]

        # Recorrido en profundidad; cada nodo se pide una vez.
        while stack:
            slug, is_root = stack.pop()
            if slug in visited:
                continue
            visited.add(slug)
            html = await get(_url(store, slug, 1))
            if not html:
                continue
            text = rsc_text(html)
            count, kids = navigator(html, text)
            if is_root and count:
                declared += count
            for rec in emit(parse_products(store, html, text)):
                stats.records += 1
                yield rec
                if limit and stats.records >= limit:
                    stats.coverage_note = note(f"parcial: límite de {limit} registros")
                    return
            kids = [(s, c) for s, c in kids if c > 0 and s not in visited]
            if count is not None and count > PAGE * PAGE_CAP and kids:
                gaps += max(0, count - sum(c for _, c in kids))
                stack.extend((s, False) for s, _ in reversed(kids))
                continue
            if count is not None and count > PAGE * PAGE_CAP:
                gaps += count - PAGE * PAGE_CAP
            # sin navegador: paginar hasta una página corta
            last = PAGE_CAP if count is None else min(PAGE_CAP, math.ceil(count / PAGE))
            for page in range(2, last + 1):
                html = await get(_url(store, slug, page))
                if not html:
                    break
                recs = parse_products(store, html)
                for rec in emit(recs):
                    stats.records += 1
                    yield rec
                    if limit and stats.records >= limit:
                        stats.coverage_note = note(f"parcial: límite de {limit} registros")
                        return
                if len(recs) < PAGE:
                    break
            stats.coverage_note = note()
    except _Budget:
        pending = len({s for s, _ in stack} - visited)
        stats.coverage_note = note(f"parcial: presupuesto de {max_pages} páginas agotado, "
                                   f"{pending} categorías sin visitar")
        return
    except StoreBlocked:
        stats.errors += 1
        stats.coverage_note = note() + " (cortada por errores seguidos)"
        return
    stats.coverage_note = note("árbol completo")
