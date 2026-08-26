"""Cliente VTEX async.

Usa los endpoints públicos de catálogo VTEX (sin auth). Todas las tiendas se
consultan en paralelo con httpx.AsyncClient. Un fallo o timeout en una tienda
no debe tumbar al resto: se reporta como "No disponible".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from urllib.parse import quote

import httpx

from . import cache, relevance
from .stores import Store

logger = logging.getLogger("gt_compare")

SEARCH_PATH = "/api/catalog_system/pub/products/search"

# Algunos WAF de VTEX rechazan requests sin un User-Agent de navegador.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Normalización de precios en centavos.
#
# El spec pedía: "si Price > 10000, asumir centavos y dividir entre 100".
# VERIFICADO contra las APIs reales (Cemaco/Walmart): commertialOffer.Price ya
# viene en quetzales (p.ej. 1399.0 = Q1,399.00; un TV de 85" reporta 11499.0 =
# Q11,499.00). Aplicar el umbral dividiría productos legítimamente caros
# (Q11,499 -> Q114.99), corrompiendo el dato.
#
# Por eso la normalización por centavos está DESACTIVADA por defecto. Si en el
# futuro alguna tienda sí entrega centavos, poner NORMALIZE_CENTS = True.
NORMALIZE_CENTS = False
CENTS_THRESHOLD = 100000


@dataclass
class Product:
    store_key: str
    store_name: str
    name: str
    price: float | None
    available: int
    url: str
    image: str | None


@dataclass
class StoreResult:
    store: Store
    products: list[Product]
    ok: bool
    error: str | None = None
    # Edad en segundos si estos productos vienen del último snapshot conocido
    # en vez de una consulta en vivo. None = en vivo.
    stale_age: int | None = None


def _dedupe_products(products: list[Product]) -> list[Product]:
    deduped: list[Product] = []
    seen: set[tuple[str, str, str]] = set()
    for p in products:
        key = (
            p.store_key,
            p.url.strip().lower() if p.url else p.name.strip().lower(),
            str(p.price or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


# Con esta cantidad de coincidencias relevantes en la primera consulta ya no
# hace falta ampliar la búsqueda con variantes.
ENOUGH_RELEVANT = 5


async def _search_with_aliases(
    fetcher,
    client,
    store: Store,
    query: str,
    plan=None,
    **kw,
) -> StoreResult:
    if plan is not None and getattr(plan, "search_queries", None):
        queries = list(plan.search_queries[:6])
    else:
        queries = relevance.search_queries(query, limit=4)
    if len(queries) <= 1:
        return await fetcher(client, store, query, **kw)

    # Antes se disparaban TODAS las variantes en paralelo contra la misma
    # tienda: "televisor samsung" son tres peticiones simultáneas a un mismo
    # host, y así es como Kemik nos empezó a responder 429. Se consulta primero
    # la frase completa y solo se amplía si hizo falta, que es el caso para el
    # que se agregaron las variantes (buscadores que no cruzan bien dos
    # palabras). En la mayoría de búsquedas esto es una sola petición.
    first = await fetcher(client, store, queries[0], **kw)
    if first.ok:
        relevantes = sum(
            1 for p in first.products if relevance.is_relevant(query, p.name)
        )
        if relevantes >= ENOUGH_RELEVANT:
            return first

    rest = await asyncio.gather(
        *(fetcher(client, store, q, **kw) for q in queries[1:])
    )
    results = [first, *rest]
    ok_results = [r for r in results if r.ok]
    if not ok_results:
        return results[0]

    products: list[Product] = []
    for r in ok_results:
        products.extend(r.products)
    return StoreResult(store, _dedupe_products(products), ok=True)


def _normalize_price(raw: float | int | None) -> float | None:
    if raw is None:
        return None
    price = float(raw)
    if NORMALIZE_CENTS and price > CENTS_THRESHOLD:
        price = price / 100.0
    return round(price, 2)


def _parse_products(store: Store, raw_list: list) -> list[Product]:
    products: list[Product] = []
    for item in raw_list:
        try:
            sku = (item.get("items") or [{}])[0]
            seller = (sku.get("sellers") or [{}])[0]
            offer = seller.get("commertialOffer") or {}
            images = sku.get("images") or [{}]
            products.append(
                Product(
                    store_key=store.key,
                    store_name=store.name,
                    name=item.get("productName", "—"),
                    price=_normalize_price(offer.get("Price")),
                    available=int(offer.get("AvailableQuantity", 0) or 0),
                    url=item.get("link", ""),
                    image=(images[0] or {}).get("imageUrl"),
                )
            )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            logger.warning("parse error en %s: %s", store.key, exc)
            continue
    return products


async def _fetch_raw(
    client: httpx.AsyncClient, store: Store, query: str
) -> list:
    # OJO: el WAF de varias tiendas VTEX rechaza el espacio codificado como '+'
    # ("Bad Request! Scripts are not allowed!"). httpx codifica los espacios de
    # los query params como '+', así que construimos el ft a mano con quote()
    # para que el espacio salga como %20, que sí es aceptado.
    ft = quote(query.strip(), safe="")
    # _to=23 ⇒ hasta 24 productos (VTEX es 0-indexed e inclusivo).
    url = f"https://{store.domain}{SEARCH_PATH}?ft={ft}&_from=0&_to=23"
    resp = await client.get(url, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


async def search_store(
    client: httpx.AsyncClient,
    store: Store,
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
) -> StoreResult:
    """Busca en una tienda; nunca lanza: encapsula errores en StoreResult."""
    ck = cache.make_key(store.key, query)
    if use_cache:
        cached = cache.get(ck, ttl_seconds)
        if cached is not None:
            return StoreResult(store, _parse_products(store, cached), ok=True)

    try:
        raw = await asyncio.wait_for(
            _fetch_raw(client, store, query), timeout=timeout
        )
        # Si 0 resultados, reintentar con la primera palabra del query.
        if not raw and " " in query.strip():
            simplified = query.strip().split()[0]
            logger.info("0 resultados en %s, reintento con '%s'", store.key, simplified)
            raw = await asyncio.wait_for(
                _fetch_raw(client, store, simplified), timeout=timeout
            )
        if use_cache:
            cache.set(ck, raw)
        return StoreResult(store, _parse_products(store, raw), ok=True)
    except asyncio.TimeoutError:
        logger.error("timeout en %s tras %ss", store.key, timeout)
        return StoreResult(store, [], ok=False, error="timeout")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        logger.error("HTTP %s en %s", code, store.key)
        hint = " (posible bot-detection)" if code in (403, 429) else ""
        return StoreResult(store, [], ok=False, error=f"HTTP {code}{hint}")
    except Exception as exc:  # noqa: BLE001 - aislar cualquier fallo de red
        logger.error("error en %s: %s", store.key, exc)
        return StoreResult(store, [], ok=False, error=str(exc))


async def search_all(
    stores: list[Store],
    query: str,
    *,
    timeout: int,
    ttl_seconds: int,
    use_cache: bool = True,
    plan=None,
) -> list[StoreResult]:
    """Consulta todas las tiendas VTEX en paralelo."""
    from . import scraper  # import lazy para evitar el ciclo vtex<->scraper

    from .stores import ensure_config
    has_max_headers = bool(ensure_config().get("max_headers"))

    kw = dict(timeout=timeout, ttl_seconds=ttl_seconds, use_cache=use_cache)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = []
        for s in stores:
            if s.kind == "vtex":
                # Max es VTEX pero está tras WAF: si el usuario capturó headers
                # de la app móvil (max_headers), enruta por el fetch dedicado.
                if s.key == "max" and has_max_headers:
                    tasks.append(_search_with_aliases(scraper.fetch_max, client, s, query, plan=plan, **kw))
                else:
                    tasks.append(_search_with_aliases(search_store, client, s, query, plan=plan, **kw))
            elif s.kind == "magento":
                tasks.append(_search_with_aliases(scraper.fetch_magento, client, s, query, plan=plan, **kw))
            elif s.kind == "kemik":
                tasks.append(_search_with_aliases(scraper.fetch_kemik, client, s, query, plan=plan, **kw))
            elif s.kind == "pricesmart":
                tasks.append(_search_with_aliases(scraper.fetch_pricesmart, client, s, query, plan=plan, **kw))
            elif s.kind == "max":
                tasks.append(_search_with_aliases(scraper.fetch_max_constructor, client, s, query, plan=plan, **kw))
            elif s.kind == "intelaf":
                tasks.append(_search_with_aliases(scraper.fetch_intelaf, client, s, query, plan=plan, **kw))
            elif s.kind == "novex":
                tasks.append(_search_with_aliases(scraper.fetch_novex, client, s, query, plan=plan, **kw))
            elif s.kind == "woocommerce":
                tasks.append(_search_with_aliases(scraper.fetch_woocommerce, client, s, query, plan=plan, **kw))
            else:
                tasks.append(_search_with_aliases(scraper.search_store, client, s, query, plan=plan, **kw))
        results = await asyncio.gather(*tasks)

    return [_with_last_known(r, query, use_cache=use_cache) for r in results]


# Cuánto se acepta servir un precio guardado cuando la tienda no responde.
# Más allá de esto preferimos declarar la tienda caída: un precio de ayer
# todavía orienta, uno de la semana pasada engaña.
STALE_MAX_AGE_SECONDS = 6 * 60 * 60


def _snapshot_key(store_key: str, query: str) -> str:
    return cache.make_key(store_key, query) + "::snapshot"


def _with_last_known(res: StoreResult, query: str, *, use_cache: bool) -> StoreResult:
    """Guarda el último resultado bueno de cada tienda y lo usa si falla.

    En Vercel el caché en disco vive en /tmp, que es efímero y por instancia,
    así que en la práctica cada arranque en frío vuelve a consultar las 13
    tiendas. Kemik responde 429 a esas ráfagas y desaparecía del comparador
    justo cuando solía ser el más barato. Con esto la tienda sigue apareciendo,
    etiquetada con la antigüedad del dato.
    """
    if not use_cache:
        return res
    key = _snapshot_key(res.store.key, query)
    if res.ok and res.products:
        cache.set(key, [asdict(p) for p in res.products])
        return res
    if res.ok:
        return res  # respondió bien pero sin coincidencias: no es una caída
    data, age = cache.get_stale(key, STALE_MAX_AGE_SECONDS)
    if not data:
        return res
    try:
        products = [Product(**d) for d in data]
    except TypeError:
        return res
    return StoreResult(res.store, products, ok=True, stale_age=age)
