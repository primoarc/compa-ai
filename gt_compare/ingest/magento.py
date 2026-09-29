"""Enumerador Magento (La Curacao, RadioShack, Steren, EPA) por categorías.

Descubre las categorías en el menú de la página de inicio y recorre cada
listado con ?p=N hasta que no hay página siguiente. No se usa
product_list_limit ni la búsqueda: el robots.txt de las cuatro tiendas los
prohíbe (EPA ya usa el máximo, 32, por defecto). Steren además prohíbe toda URL
con "?", así que ahí solo se lee la primera página de cada categoría. Si el
menú no da categorías, la corrida termina: no se cae a búsquedas prohibidas.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from urllib.parse import urljoin, urlsplit

from ..scraper import _RE_IMG, _RE_LINK, _RE_OLD_PRICE, _RE_PRICE, _RE_TAG
from ..stores import Store
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord

logger = logging.getLogger("gt_compare.ingest")

# Tope de seguridad por categoría (EPA, la más grande, ronda 300 páginas en total).
MAX_PAGES_PER_CATEGORY = 400

# Categorías de Unicomer que son subconjuntos de otras o no son productos.
_UNICOMER_SKIP = {"gift-card", "promociones-gt", "lo-mas-nuevo"}

# Respaldo si el menú no expone categorías.
# Lo que manda un navegador al pedir una página (httpx manda "Accept: */*").
# No cambió los 406 de La Curacao y RadioShack desde GitHub: eso es su WAF.
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Tiendas cuyo robots.txt prohíbe cualquier URL con "?" (Steren: "Disallow: /*?").
NO_QUERY_STRING = {"steren"}

# Cada producto del listado es un <li class="item product product-item">.
_RE_TILE_START = re.compile(r'<li\b[^>]*class="[^"]*\bproduct-item\b[^"]*"', re.I)
_RE_FINAL_PRICE = re.compile(
    r'data-price-amount="([\d.]+)"[^>]*data-price-type="finalPrice"'
    r'|data-price-type="finalPrice"[^>]*data-price-amount="([\d.]+)"',
    re.I,
)
_RE_PRODUCT_ID = re.compile(
    r'data-product-id="(\d+)"|data-price-box="product-id-(\d+)"'
    r'|name="product"\s+value="(\d+)"|product-item-info_(\d+)',
    re.I,
)
_RE_SKU = re.compile(
    r'data-product-sku="([^"]+)"|class="product-item-sku[^"]*"[^>]*>\s*([^<\s][^<]*?)\s*<',
    re.I,
)
_RE_NEXT = re.compile(r'pages-item-next|class="action\s+next"', re.I)
_RE_TITLE = re.compile(r'data-ui-id="page-title-wrapper"[^>]*>([^<]+)<', re.I)
_RE_TOTAL = re.compile(r'<span class="toolbar-number">([\d,.]+)</span>\s*(?:de\s*)?Resultados', re.I)
_RE_TOOLBAR_NUMS = re.compile(r'class="toolbar-number[^"]*">([\d,.]+)<', re.I)

_RE_UNICOMER_CAT = r'href="https://{domain}{base}c/([a-z0-9\-]+)"'
_RE_LUMA_L1 = re.compile(
    r'<li\s+class="nav-1-\d+(?:&#x20;|\s)[^"]*category-item[^"]*"[^>]*>\s*'
    r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_RE_REL_PATH = re.compile(r'href="(/[a-z0-9\-]+(?:/[a-z0-9\-]+)?)"', re.I)


@dataclass
class Category:
    url: str
    name: str


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_RE_TAG.sub(" ", text))).strip()


def _first_group(m: Optional[re.Match]) -> Optional[str]:
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def _as_price(raw: Optional[str]) -> Optional[float]:
    try:
        return round(float(raw), 2) if raw else None
    except ValueError:
        return None


def _base_path(store: Store) -> str:
    """Prefijo de la tienda: '/guatemala/' en Unicomer, '/' en las demás."""
    path = (store.search_path or "/").split("?")[0]
    for marker in ("catalogsearch/", "search/"):
        if marker in path:
            return path.split(marker)[0] or "/"
    return "/"


def _tiles(html: str) -> list[str]:
    starts = [m.start() for m in _RE_TILE_START.finditer(html)]
    if not starts:
        # Plantillas sin <li product-item>: mismo corte que scraper._parse_magento.
        return re.split(r"product-item-info", html, flags=re.I)[1:]
    # El último producto termina con la lista (</ol>), no con el pie de página.
    end = html.find("</ol>", starts[-1])
    return [html[a:b] for a, b in zip(starts, starts[1:] + [end if end > 0 else len(html)])]


def parse_listing(store: Store, html: str, category: Optional[str] = None) -> list[ProductRecord]:
    """Registros de un listado Magento (categoría o búsqueda)."""
    out: list[ProductRecord] = []
    base = f"https://{store.domain}/"
    for tile in _tiles(html):
        link = _RE_LINK.search(tile)
        if not link:
            continue
        name = _clean(link.group(2))
        if not name:
            continue
        # El orden de finalPrice/oldPrice cambia por tienda (Steren pone oldPrice primero).
        price = _as_price(_first_group(_RE_FINAL_PRICE.search(tile)))
        if price is None:
            m = _RE_PRICE.search(tile)
            price = _as_price(m.group(1) if m else None)
        if price is None:
            continue
        list_price = _as_price(_first_group(_RE_OLD_PRICE.search(tile)))
        if list_price is not None and list_price <= price:
            list_price = None
        url = urljoin(base, html_lib.unescape(link.group(1)))
        pid = _first_group(_RE_PRODUCT_ID.search(tile))
        low = tile.lower()
        agotado = "agotado" in low or "sin existencia" in low
        img = _RE_IMG.search(tile)
        sku = _first_group(_RE_SKU.search(tile))
        extra = {}
        if pid:
            extra["product_id"] = pid
        if sku:
            extra["sku"] = _clean(sku)
        out.append(
            ProductRecord(
                store_key=store.key,
                store_sku=pid or urlsplit(url).path,
                url=url,
                name=name,
                price=price,
                list_price=list_price,
                available=0 if agotado else 1,
                image=html_lib.unescape(img.group(1)) if img else None,
                store_category=category,
                first_sku=True,
                extra=extra,
            )
        )
    return out


def discover_categories(store: Store, home: str) -> list[Category]:
    """Categorías del menú, según la plantilla de cada tienda."""
    base = _base_path(store)
    root = f"https://{store.domain}"
    seen: set[str] = set()
    cats: list[Category] = []

    def add(url: str, name: str) -> None:
        url = urljoin(root + "/", html_lib.unescape(url))
        if url not in seen:
            seen.add(url)
            cats.append(Category(url, name))

    # Unicomer: /guatemala/c/<raíz>; las raíces son "anchor" e incluyen subcategorías.
    pat = re.compile(_RE_UNICOMER_CAT.format(domain=re.escape(store.domain), base=re.escape(base)), re.I)
    for slug in pat.findall(home):
        if slug not in _UNICOMER_SKIP:
            add(f"{root}{base}c/{slug}", slug)
    if cats:
        return cats

    # Luma (EPA): hijos de primer nivel de la raíz "Productos".
    for href, text in _RE_LUMA_L1.findall(home):
        add(href, _clean(text))
    if cats:
        return cats

    # Megamenú con rutas relativas (Steren): las raíces son portadas sin
    # productos, así que se recorren las hojas /raiz/hoja y las raíces sin hijos.
    paths = list(dict.fromkeys(_RE_REL_PATH.findall(home)))
    roots = {p.strip("/") for p in paths if p.count("/") == 1}
    parents = {p.split("/")[1] for p in paths if p.count("/") == 2 and p.split("/")[1] in roots}
    for p in paths:
        if p.count("/") == 2 and p.split("/")[1] in parents:
            add(p, p.strip("/"))
    if cats:
        # Raíces sin hijos (p.ej. /lo-nuevo); una página CMS solo cuesta una petición vacía.
        for p in paths:
            if p.count("/") == 1 and p.strip("/") not in parents:
                add(p, p.strip("/"))
    return cats


def _page_url(url: str, page: int) -> str:
    if page <= 1:
        return url
    return f"{url}{'&' if '?' in url else '?'}p={page}"


def _declared_total(html: str) -> Optional[int]:
    m = _RE_TOTAL.search(html)
    nums = [m.group(1)] if m else _RE_TOOLBAR_NUMS.findall(html)[-1:]
    try:
        return int(nums[0].replace(",", "").replace(".", "")) if nums else None
    except ValueError:
        return None


async def enumerate_magento(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
) -> AsyncIterator[ProductRecord]:
    base = _base_path(store)
    seen: set[str] = set()
    done = 0
    declared = 0
    mode = "categorías del menú"
    sources: list[Category] = []
    cut = False

    def note() -> None:
        extra = f", {declared:,} declarados" if declared else ""
        stats.coverage_note = (
            f"parcial: {done} de {len(sources)} {mode}, {stats.records:,} productos{extra}"
        )
        if limit and stats.records >= limit:
            stats.coverage_note += f" (límite {limit})"
        if cut:
            stats.coverage_note += " (cortada por errores seguidos)"

    try:
        resp = await client.get(f"https://{store.domain}{base}", headers=HTML_HEADERS)
        stats.pages += 1
        if resp.status_code == 200:
            sources = discover_categories(store, resp.text)
        else:
            stats.errors += 1
        if not sources:
            mode = "categorías (el menú no dio ninguna)"
        logger.info("%s: %s %s", store.key, len(sources), mode)
        max_pages = 1 if store.key in NO_QUERY_STRING else MAX_PAGES_PER_CATEGORY

        for cat in sources:
            cat_ids: set[str] = set()
            for page in range(1, max_pages + 1):
                resp = await client.get(_page_url(cat.url, page), headers=HTML_HEADERS)
                stats.pages += 1
                if resp.status_code != 200:
                    stats.errors += 1
                    break
                html = resp.text
                if page == 1:
                    title = _RE_TITLE.search(html)
                    if title:
                        cat.name = _clean(title.group(1))
                    declared += _declared_total(html) or 0
                recs = parse_listing(store, html, cat.name)
                # Magento repite la última página si p se pasa: sin ids nuevos, fin.
                fresh = [r for r in recs if r.store_sku not in cat_ids]
                if not fresh:
                    break
                for rec in fresh:
                    cat_ids.add(rec.store_sku)
                    if rec.store_sku in seen:
                        continue
                    seen.add(rec.store_sku)
                    stats.records += 1
                    yield rec
                    if limit and stats.records >= limit:
                        return
                if not _RE_NEXT.search(html):
                    break
            done += 1
    except StoreBlocked:
        stats.errors += 1
        cut = True
    finally:
        note()
