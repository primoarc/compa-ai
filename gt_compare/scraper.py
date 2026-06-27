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
    r'class="product-item-link"\s+href="([^"]+)"\s*>([^<]+)<', re.I
)
_RE_PRICE = re.compile(
    r'data-price-amount="([\d.]+)"\s+data-price-type="finalPrice"', re.I
)
_RE_IMG = re.compile(r'class="product-image-photo"[^>]*\ssrc="([^"]+)"', re.I)


def _parse_magento(store: Store, html: str) -> list[Product]:
    products: list[Product] = []
    # Cada chunk arranca en un "product-item-info" y termina donde empieza el
    # siguiente; así el precio que capturamos pertenece a ese producto.
    chunks = _RE_ITEM.split(html)[1:]
    for chunk in chunks:
        link = _RE_LINK.search(chunk)
        if not link:
            continue
        url, name = link.group(1), html_lib.unescape(link.group(2)).strip()
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
    f"pid,title,brand,slug,thumb_image,currency,fractionDigits,"
    f"{_PS_PRICE_F},{_PS_ORIG_F},{_PS_INV_F}"
)


def _parse_pricesmart(store: Store, payload: dict) -> list[Product]:
    products: list[Product] = []
    for d in payload.get("response", {}).get("docs", []):
        raw = d.get(_PS_PRICE_F)
        if raw is None:
            price = None
        else:
            # fractionDigits indica los decimales (centavos). Default 2.
            digits = int(d.get("fractionDigits") or 2)
            price = round(float(raw) / (10 ** digits), 2)
        slug = d.get("slug") or d.get("pid", "")
        url = f"https://{store.domain}/es-gt/producto/{slug}/{d.get('pid','')}"
        products.append(
            Product(
                store_key=store.key,
                store_name=store.name,
                name=d.get("title", "—"),
                price=price,
                available=1 if str(d.get(_PS_INV_F, "")).lower() == "in stock" else 0,
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


# --- Max Distelsa: VTEX detrás de WAF -------------------------------------
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
