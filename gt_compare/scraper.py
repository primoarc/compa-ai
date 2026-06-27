"""Placeholder para tiendas no-VTEX (Kemik, Novex, etc.).

Estas tiendas no exponen la API de catálogo VTEX, así que requieren scraping
de HTML. La interfaz devuelve los mismos objetos Product que vtex.py para que
el resto del pipeline (cache, display) funcione sin cambios.

Implementación pendiente: el siguiente prompt agrega Kemik y Novex.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
from urllib.parse import quote

import httpx

from . import cache
from .stores import Store, ensure_config
from .vtex import HEADERS, SEARCH_PATH, Product, StoreResult, _parse_products

logger = logging.getLogger("gt_compare")


def _as_price(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, str):
            raw = raw.replace(",", "").strip()
        return round(float(raw), 2)
    except (TypeError, ValueError):
        return None


async def search_store(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    """No implementado todavía (Kemik/Novex). Resultado vacío marcado como error."""
    return StoreResult(
        store, [], ok=False, error="scraper no implementado"
    )


# --- Tiendas Magento de Grupo Unicomer (scraping HTML) --------------------
#
# La Curacao (lacuracaonline.com) y RadioShack (radioshackla.com) comparten la
# misma plataforma Magento y operan la tienda GT en /guatemala/. GraphQL está
# deshabilitado (403 "GraphQL disabled"), así que parseamos el HTML del listado
# de búsqueda: GET {search_path}{query}  (p.ej. /guatemala/search/televisor).
# Cada producto es un <div class="product-item-info"> con un product-item-link
# (nombre + URL) y un data-price-amount type="finalPrice" en quetzales.

_RE_ITEM = re.compile(r'product-item-info', re.I)
_RE_LINK = re.compile(
    r'<a\b[^>]*class="[^"]*product-item-link[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_RE_PRICE = re.compile(
    r'data-price-amount="([\d.]+)"', re.I
)
_RE_IMG = re.compile(r'class="product-image-photo"[^>]*\ssrc="([^"]+)"', re.I)
_RE_TAG = re.compile(r"<[^>]+>")


def _parse_magento(store: Store, html: str) -> list[Product]:
    products: list[Product] = []
    # Cada chunk arranca en un "product-item-info" y termina donde empieza el
    # siguiente; así el precio que capturamos pertenece a ese producto.
    chunks = _RE_ITEM.split(html)[1:]
    for chunk in chunks:
        link = _RE_LINK.search(chunk)
        if not link:
            continue
        url = link.group(1)
        name = html_lib.unescape(_RE_TAG.sub(" ", link.group(2))).strip()
        name = re.sub(r"\s+", " ", name)
        if not name:
            continue
        price_m = _RE_PRICE.search(chunk)
        price = float(price_m.group(1)) if price_m else None
        img_m = _RE_IMG.search(chunk)
        agotado = "agotado" in chunk.lower() or "sin existencia" in chunk.lower()
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=name,
                price=round(price, 2) if price is not None else None,
                available=0 if agotado else 1,
                url=url,
                image=img_m.group(1) if img_m else None,
            )
        )
    return products


# --- Kemik (Next.js, SSR) -------------------------------------------------
#
# Kemik corre sobre Next.js. La búsqueda hace GET a /search?query={q} y el
# servidor renderiza (SSR) las tarjetas de producto en el HTML, así que basta
# httpx — no se necesita navegador ni token. La API interna (/public/search)
# sí exige un Authorization Bearer, por eso scrapeamos el HTML SSR.
#
# Cada tarjeta es: <a title="NOMBRE" ... href="/slug"> ... <img src="cdn..."> ...
#   <div data-component="Price"><div>Q1,469</div>...

_K_ANCHOR = re.compile(r'<a title="([^"]*)"[^>]*href="(/[a-z0-9][a-z0-9\-]{20,})"', re.I)
_K_PRICE = re.compile(r'data-component="Price"[^>]*>\s*<div>Q\s?([\d,]+)</div>', re.I)
_K_IMG = re.compile(r'<img\s+src="(https://cdn\.kemik\.gt[^"]+)"', re.I)


def _parse_kemik(store: Store, html: str) -> list[Product]:
    products: list[Product] = []
    anchors = list(_K_ANCHOR.finditer(html))
    for idx, m in enumerate(anchors):
        end = anchors[idx + 1].start() if idx + 1 < len(anchors) else m.end() + 4000
        seg = html[m.end():end]
        pm = _K_PRICE.search(seg)
        if not pm:
            continue
        im = _K_IMG.search(seg)
        agotado = "agotado" in seg.lower() or "sin stock" in seg.lower()
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=html_lib.unescape(m.group(1)).strip(),
                price=round(float(pm.group(1).replace(",", "")), 2),
                available=0 if agotado else 1,
                url=f"https://{store.domain}{m.group(2)}",
                image=im.group(1) if im else None,
            )
        )
    return products


async def fetch_kemik(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_kemik(store, cached), ok=True)

    async def _get(q: str) -> str:
        url = f"https://{store.domain}/search?query={quote(q.strip())}"
        resp = await asyncio.wait_for(
            client.get(url, headers=HEADERS), timeout=timeout
        )
        resp.raise_for_status()
        return resp.text

    try:
        html = await _get(query)
        products = _parse_kemik(store, html)
        if not products and " " in query.strip():
            html = await _get(query.strip().split()[0])
            products = _parse_kemik(store, html)
        if use_cache:
            cache.set(ck, html)
        return StoreResult(store, products, ok=True)
    except asyncio.TimeoutError:
        return StoreResult(store, [], ok=False, error="timeout")
    except httpx.HTTPStatusError as exc:
        logger.error("kemik HTTP %s", exc.response.status_code)
        return StoreResult(store, [], ok=False, error=f"HTTP {exc.response.status_code}")
    except Exception as exc:  # noqa: BLE001
        logger.error("kemik error: %s", exc)
        return StoreResult(store, [], ok=False, error=str(exc))


# --- PriceSmart: Bloomreach (búsqueda) + commercetools (precios) ----------
#
# CORRECCIÓN: PriceSmart SÍ muestra precios públicos en GT (sin login). El
# precio NO está en el feed de búsqueda de Bloomreach (ahí price = 0.0); viene
# de COMMERCETOOLS, el sistema de precios del canal GT. PriceSmart (Nuxt) une
# ambos server-side con su propio proxy y un token de invitado:
#   - Búsqueda/catálogo: Bloomreach Discovery
#       https://core.dxpapi.com/api/v1/core/
#       account_id=7024, domain_key=pricesmart_bloomreach_io_es, view_id=GT
#   - Precios: commercetools vía el proxy POST /api/br_discovery/... y /api/ct/*
#     (requiere el token de invitado que el servidor genera; replicarlo desde
#      httpx exige reversar ese flujo).
#
# Por eso queda DESHABILITADA *por ahora*: el fetch directo a dxpapi (abajo)
# trae nombres/URLs pero precio 0. Para precios reales hacen falta dos rutas
# posibles (ver README): (A) reversar el flujo commercetools de invitado, o
# (B) renderizar la página en navegador headless y leer el DOM.
#
# Parámetros extraídos del payload Nuxt de www.pricesmart.com/es-gt.

# Parámetros del lado Bloomreach (reversados del payload Nuxt + chunk
# caa0f69.modern.js de www.pricesmart.com/es-gt):
_PS_ENDPOINT = "https://core.dxpapi.com/api/v1/core/"
_PS_ACCOUNT = "7024"
_PS_AUTH_KEY = "ev7libhybjg5h1d1"          # brDiscoveryAuthKey
_PS_DOMAIN_KEY = "pricesmart_bloomreach_io_es"
_PS_VIEW_ID = "GT"                          # localiza catálogo a Guatemala
_PS_CLUB = "6303"                           # club por defecto (vsf-selected-club)

# CLAVE: el precio NO está en el campo genérico `price` (siempre 0), sino en
# campos específicos por país+club: `price_GT_6303`, `inventory_GT_6303`, etc.
# (sufijo {view_id}_{club}). `fractionDigits`=2 ⇒ el precio viene en CENTAVOS,
# hay que dividir entre 100. No se necesita commercetools ni token de sesión:
# la API pública de dxpapi con el `fl` correcto ya trae todo.
_PS_PRICE_F = f"price_{_PS_VIEW_ID}_{_PS_CLUB}"
_PS_ORIG_F = f"original_price_without_saving_{_PS_VIEW_ID}_{_PS_CLUB}"
_PS_INV_F = f"inventory_{_PS_VIEW_ID}_{_PS_CLUB}"
_PS_FL = (
    f"pid,title,brand,slug,thumb_image,currency,fractionDigits,master_sku,variants,"
    f"{_PS_PRICE_F},{_PS_ORIG_F},{_PS_INV_F}"
)


def _ps_first_scalar(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _ps_variant(d: dict) -> dict:
    master = d.get("master_sku")
    variants = d.get("variants") or []
    if master:
        for variant in variants:
            if variant.get("skuid") == master:
                return variant
    for variant in variants:
        if variant.get(_PS_PRICE_F) is not None:
            return variant
    return {}


def _ps_price(d: dict) -> float | None:
    raw = d.get(_PS_PRICE_F)
    if raw is None:
        raw = _ps_first_scalar(_ps_variant(d).get(_PS_PRICE_F))
    if raw is None:
        return None
    digits = int(d.get("fractionDigits") or 2)
    return round(float(raw) / (10 ** digits), 2)


def _ps_available(d: dict) -> int:
    raw = d.get(_PS_INV_F)
    if raw is None:
        raw = _ps_first_scalar(_ps_variant(d).get(_PS_INV_F))
    return 1 if str(raw or "").lower() == "in stock" else 0


def _parse_pricesmart(store: Store, payload: dict) -> list[Product]:
    products: list[Product] = []
    for d in payload.get("response", {}).get("docs", []):
        price = _ps_price(d)
        slug = d.get("slug") or d.get("pid", "")
        sku = d.get("master_sku") or d.get("pid", "")
        url = f"https://{store.domain}/es-gt/producto/{slug}/{sku}"
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=d.get("title", "—"),
                price=price,
                available=_ps_available(d),
                url=url,
                image=d.get("thumb_image"),
            )
        )
    return products


async def fetch_pricesmart(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_pricesmart(store, cached), ok=True)

    params = {
        "account_id": _PS_ACCOUNT, "auth_key": _PS_AUTH_KEY,
        "domain_key": _PS_DOMAIN_KEY, "view_id": _PS_VIEW_ID,
        "request_type": "search", "search_type": "keyword",
        "fl": _PS_FL, "q": query.strip(), "rows": "24", "start": "0",
        "request_id": "1", "_br_uid_2": "uid%3D1",
        "url": f"https://{store.domain}/es-gt",
    }
    try:
        resp = await asyncio.wait_for(
            client.get(_PS_ENDPOINT, params=params, headers=HEADERS), timeout=timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        if use_cache:
            cache.set(ck, payload)
        return StoreResult(store, _parse_pricesmart(store, payload), ok=True)
    except asyncio.TimeoutError:
        return StoreResult(store, [], ok=False, error="timeout")
    except Exception as exc:  # noqa: BLE001
        logger.error("pricesmart error: %s", exc)
        return StoreResult(store, [], ok=False, error=str(exc))


async def fetch_magento(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    path = store.search_path or "/guatemala/search/"

    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_magento(store, cached), ok=True)

    async def _get(q: str) -> str:
        url = f"https://{store.domain}{path}{quote(q.strip())}"
        resp = await asyncio.wait_for(
            client.get(url, headers=HEADERS), timeout=timeout
        )
        resp.raise_for_status()
        return resp.text

    try:
        html = await _get(query)
        products = _parse_magento(store, html)
        # Fallback: si 0 resultados y el query tiene varias palabras, reintentar
        # con la primera (mismo criterio que vtex.py).
        if not products and " " in query.strip():
            html = await _get(query.strip().split()[0])
            products = _parse_magento(store, html)
        if use_cache:
            cache.set(ck, html)
        return StoreResult(store, products, ok=True)
    except asyncio.TimeoutError:
        return StoreResult(store, [], ok=False, error="timeout")
    except httpx.HTTPStatusError as exc:
        logger.error("%s HTTP %s", store.key, exc.response.status_code)
        return StoreResult(store, [], ok=False, error=f"HTTP {exc.response.status_code}")
    except Exception as exc:  # noqa: BLE001
        logger.error("%s error: %s", store.key, exc)
        return StoreResult(store, [], ok=False, error=str(exc))


# --- Intelaf: API pública usada por su frontend Next.js -------------------

_INTELAF_ENDPOINT = "https://api.intelaf.com:2053/app/api/producto/busqueda"


def _intelaf_price(item: dict) -> float | None:
    discount = _as_price(item.get("PrecioDescuento"))
    if discount and discount > 0:
        return discount
    return _as_price(item.get("PrecioNormal"))


def _parse_intelaf(store: Store, payload: dict) -> list[Product]:
    products: list[Product] = []
    data = payload.get("Response", payload)
    for item in data.get("Productos", []) or []:
        code = item.get("Codigo") or ""
        if not code:
            continue
        stock = sum(float(x.get("Existencia") or 0) for x in item.get("Existencia", []) or [])
        if item.get("EnBodega"):
            stock += 1
        if item.get("EnTransito"):
            stock += 1
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=html_lib.unescape(item.get("Descripcion") or code).strip(),
                price=_intelaf_price(item),
                available=int(stock),
                url=f"https://{store.domain}/producto/{quote(code, safe='')}",
                image=item.get("Imagen"),
            )
        )
    return products


async def fetch_intelaf(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_intelaf(store, cached), ok=True)

    payload = {
        "PrecioMenor": 0,
        "PrecioMayor": 100000,
        "Marcas": [],
        "SucursalesCodigo": [],
        "Orden": "default",
        "CantidadMaxima": 24,
        "Query": query.strip(),
        "Categorias": [],
        "Pagina": 1,
        "Acendente": True,
        "Instruccion": {"nombre": "busqueda", "valor": ""},
        "NumeroRecienIngreso": 0,
    }
    headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": f"https://{store.domain}",
        "Referer": f"https://{store.domain}/",
    }
    try:
        resp = await asyncio.wait_for(
            client.post(_INTELAF_ENDPOINT, headers=headers, json=payload), timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if use_cache:
            cache.set(ck, data)
        return StoreResult(store, _parse_intelaf(store, data), ok=True)
    except asyncio.TimeoutError:
        return StoreResult(store, [], ok=False, error="timeout")
    except Exception as exc:  # noqa: BLE001
        logger.error("intelaf error: %s", exc)
        return StoreResult(store, [], ok=False, error=str(exc))


# --- Novex: Doofinder, el motor público usado por su buscador ------------

_NOVEX_DOOFINDER = "https://us1-search.doofinder.com/5/search"
_NOVEX_HASHID = "a57e788687f5cb996139456454036d97"


def _parse_novex(store: Store, payload: dict) -> list[Product]:
    products: list[Product] = []
    for item in payload.get("results", []) or []:
        sku = str(item.get("id") or item.get("mpn") or "").strip()
        title = item.get("title") or sku
        price = _as_price(item.get("sale_price") or item.get("best_price") or item.get("price"))
        available = 1 if str(item.get("availability", "")).lower() == "in stock" else 0
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=html_lib.unescape(str(title)).strip(),
                price=price,
                available=available,
                url=item.get("link") or f"https://{store.domain}/producto/{quote(sku, safe='')}",
                image=item.get("image_link"),
            )
        )
    return products


async def fetch_novex(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_novex(store, cached), ok=True)

    params = {
        "hashid": _NOVEX_HASHID,
        "query": query.strip(),
        "rpp": "24",
    }
    headers = {
        **HEADERS,
        "Accept": "application/json",
        "Origin": f"https://{store.domain}",
        "Referer": f"https://{store.domain}/",
    }
    try:
        resp = await asyncio.wait_for(
            client.get(_NOVEX_DOOFINDER, headers=headers, params=params), timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if use_cache:
            cache.set(ck, data)
        return StoreResult(store, _parse_novex(store, data), ok=True)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return StoreResult(store, [], ok=False, error="timeout")
    except Exception as exc:  # noqa: BLE001
        logger.error("novex error: %s", exc)
        return StoreResult(store, [], ok=False, error=str(exc))


# --- WooCommerce / WordPress ---------------------------------------------

_WC_ITEM = re.compile(r'<li\b[^>]*class="[^"]*\bproduct\b[^"]*"[^>]*>(.*?)</li>', re.I | re.S)
_WC_LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*woocommerce-loop-product__link[^>]*>', re.I | re.S)
_WC_TITLE = re.compile(r'<h2\b[^>]*class="[^"]*woocommerce-loop-product__title[^"]*"[^>]*>(.*?)</h2>', re.I | re.S)
_WC_AMOUNT = re.compile(r'woocommerce-Price-currencySymbol">\s*Q\s*</span>\s*([\d,]+(?:\.\d+)?)', re.I)
_WC_IMG = re.compile(r'\b(?:data-lazy-src|src)="(https?://[^"]+)"', re.I)


def _parse_woocommerce(store: Store, html: str) -> list[Product]:
    products: list[Product] = []
    for chunk in _WC_ITEM.findall(html):
        link_m = _WC_LINK.search(chunk)
        title_m = _WC_TITLE.search(chunk)
        if not (link_m and title_m):
            continue
        amounts = _WC_AMOUNT.findall(chunk)
        price = _as_price(amounts[-1]) if amounts else None
        img_m = _WC_IMG.search(chunk)
        agotado = "outofstock" in chunk.lower() or "agotado" in chunk.lower()
        name = html_lib.unescape(_RE_TAG.sub(" ", title_m.group(1))).strip()
        name = re.sub(r"\s+", " ", name)
        if not name:
            continue
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=name,
                price=price,
                available=0 if agotado else 1,
                url=link_m.group(1),
                image=img_m.group(1) if img_m else None,
            )
        )
    return products


async def fetch_woocommerce(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_woocommerce(store, cached), ok=True)

    url = f"https://{store.domain}/?s={quote(query.strip())}&post_type=product"
    headers = {**HEADERS, "Accept": "text/html,application/xhtml+xml"}
    try:
        resp = await asyncio.wait_for(
            client.get(url, headers=headers), timeout=timeout
        )
        resp.raise_for_status()
        html = resp.text
        products = _parse_woocommerce(store, html)
        if not products and " " in query.strip():
            url = f"https://{store.domain}/?s={quote(query.strip().split()[0])}&post_type=product"
            resp = await asyncio.wait_for(
                client.get(url, headers=headers), timeout=timeout
            )
            resp.raise_for_status()
            html = resp.text
            products = _parse_woocommerce(store, html)
        if use_cache:
            cache.set(ck, html)
        return StoreResult(store, products, ok=True)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return StoreResult(store, [], ok=False, error="timeout")
    except httpx.HTTPStatusError as exc:
        logger.error("%s HTTP %s", store.key, exc.response.status_code)
        return StoreResult(store, [], ok=False, error=f"HTTP {exc.response.status_code}")
    except Exception as exc:  # noqa: BLE001
        logger.error("%s error: %s", store.key, exc)
        return StoreResult(store, [], ok=False, error=str(exc))


# --- Max Distelsa: Constructor.io -----------------------------------------
#
# Max ya no expone su búsqueda pública como VTEX desde www.max.com.gt. El
# frontend Next.js consulta Constructor.io con una llave pública y la respuesta
# trae nombre, URL, imagen, precio final y stock. Esto es más estable que
# renderizar el sitio con navegador porque es el mismo endpoint de búsqueda que
# usa la página /search?q=...

_MAX_CONSTRUCTOR_ENDPOINT = "https://ac.cnstrc.com/search"
_MAX_CONSTRUCTOR_KEY = "key_5JqvLHPZsU80qkem"


def _max_available(data: dict) -> int:
    qty = data.get("salable_quantity")
    if qty is not None:
        try:
            return int(float(qty))
        except (TypeError, ValueError):
            pass

    for facet in data.get("facets") or []:
        if facet.get("name") == "availability":
            values = {str(v).upper() for v in facet.get("values") or []}
            return 1 if "IN_STOCK" in values else 0
    return 1


def _parse_max_constructor(store: Store, payload: dict) -> list[Product]:
    if not isinstance(payload, dict):
        return []

    products: list[Product] = []
    for item in payload.get("response", {}).get("results") or []:
        data = item.get("data") or {}
        price = (
            _as_price(data.get("final_price"))
            or _as_price(data.get("special_price"))
            or _as_price(data.get("price"))
            or _as_price(data.get("regular_price"))
        )
        url = data.get("url")
        if not url and data.get("url_key"):
            url = f"https://{store.domain}/{data['url_key']}"
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=item.get("value") or data.get("meta_title") or "—",
                price=price,
                available=_max_available(data),
                url=url or "",
                image=data.get("image_url"),
            )
        )
    return products


async def fetch_max_constructor(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_max_constructor(store, cached), ok=True)

    url = f"{_MAX_CONSTRUCTOR_ENDPOINT}/{quote(query.strip(), safe='')}"
    params = {
        "key": _MAX_CONSTRUCTOR_KEY,
        "i": "gt-compare",
        "s": "1",
        "c": "ciojs-client-2.65.0",
        "num_results_per_page": "24",
    }
    try:
        resp = await asyncio.wait_for(
            client.get(url, params=params, headers=HEADERS), timeout=timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        if use_cache:
            cache.set(ck, payload)
        return StoreResult(store, _parse_max_constructor(store, payload), ok=True)
    except asyncio.TimeoutError:
        return StoreResult(store, [], ok=False, error="timeout")
    except httpx.HTTPStatusError as exc:
        logger.error("max constructor HTTP %s", exc.response.status_code)
        return StoreResult(store, [], ok=False, error=f"HTTP {exc.response.status_code}")
    except Exception as exc:  # noqa: BLE001
        logger.error("max constructor error: %s", exc)
        return StoreResult(store, [], ok=False, error=str(exc))


# --- Max Distelsa: VTEX detrás de WAF (fallback manual viejo) --------------
#
# El endpoint VTEX de Max funciona, pero Cloudflare/Akamai bloquea cualquier
# request "limpio" con 403. La app móvil de Max NO pasa por el mismo challenge,
# así que la ruta más eficiente es:
#
#   1. Con Proxyman/mitmproxy, capturar en un dispositivo real un request de
#      búsqueda de la app de Max.
#   2. Copiar los headers EXACTOS (User-Agent del device, cookies incluyendo
#      `cf_clearance`, x-* propios de la app, etc.).
#   3. Pegarlos en ~/.gt-compare/config.yaml:
#
#        max_headers:
#          User-Agent: "MaxApp/3.2 (Android 13; ...)"
#          Cookie: "cf_clearance=...; ..."
#          x-vtex-...: "..."
#
# Con esos headers, fetch_max() golpea el mismo endpoint VTEX y reusa el parseo
# de vtex.py. Si el `cf_clearance` expira, hay que recapturar.


async def fetch_max(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    cfg = ensure_config()
    headers = cfg.get("max_headers")
    if not headers:
        return StoreResult(
            store, [], ok=False,
            error="faltan max_headers en config.yaml (ver scraper.py)",
        )

    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_products(store, cached), ok=True)

    ft = quote(query.strip(), safe="")
    url = f"https://{store.domain}{SEARCH_PATH}?ft={ft}&_from=0&_to=9"
    try:
        resp = await asyncio.wait_for(
            client.get(url, headers=headers), timeout=timeout
        )
        resp.raise_for_status()
        raw = resp.json()
        if use_cache:
            cache.set(ck, raw)
        return StoreResult(store, _parse_products(store, raw), ok=True)
    except asyncio.TimeoutError:
        return StoreResult(store, [], ok=False, error="timeout")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = " (cf_clearance expirado? recapturar)" if code == 403 else ""
        logger.error("max HTTP %s%s", code, hint)
        return StoreResult(store, [], ok=False, error=f"HTTP {code}{hint}")
    except Exception as exc:  # noqa: BLE001
        logger.error("max error: %s", exc)
        return StoreResult(store, [], ok=False, error=str(exc))
