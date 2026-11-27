"""Búsqueda en vivo de La Curacao y RadioShack (API de catálogo de Adobe). Sin red.

Se ejecuta sin dependencias:  python tests/test_search_adobe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from gt_compare import cache, catalog_search, scraper, vtex  # noqa: E402
from gt_compare.images import thumb  # noqa: E402
from gt_compare.stores import load_stores  # noqa: E402

cache.CACHE_DIR = Path(tempfile.mkdtemp())
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "adobe" / "search.json").read_text())
STORES = {s.key: s for s in load_stores()}

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


def fetch(key, handler, query="tv 65", use_cache=False):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await scraper.fetch_adobe(client, STORES[key], query, timeout=5,
                                             ttl_seconds=60, use_cache=use_cache)
    return asyncio.run(go())


requests = []


def ok(request):
    requests.append(request)
    return httpx.Response(200, json=FIXTURE)


# --- parseo ------------------------------------------------------------------
res = fetch("curacao", ok)
check("responde", (res.ok, res.error), (True, None))
check("el que no trae precio se salta", len(res.products), 2)
a, b = res.products
check("precio y lista", (a.price, a.list_price), (3398.0, 5999.0))
check("lista igual al precio no es rebaja", (b.price, b.list_price), (4100.5, None))
check("disponibilidad", (a.available, b.available), (1, 0))
check("imagen principal", a.image, "https://www.lacuracaonline.com/media/catalog/product/d/e/demo65a.jpg")
check("sin imágenes", b.image, None)
check("miniatura al tamaño que se muestra", thumb(a.image, 112), a.image + "?width=112")
check("url de la ficha", a.url,
      "https://www.lacuracaonline.com/guatemala/televisor-acme-demo65a-900000000011/p")
check("tienda", (a.store_key, a.store_name), ("curacao", "La Curacao"))

# --- petición: la misma que hace el sitio -------------------------------------
for key, prefix in (("curacao", "lco"), ("radioshack", "rso")):
    requests.clear()
    fetch(key, ok)
    req = requests[0]
    body = json.loads(req.content)["variables"]
    check(f"{key}: destino", str(req.url), "https://catalog-service.adobe.io/graphql")
    check(f"{key}: códigos de tienda",
          [req.headers[h] for h in ("Magento-Website-Code", "Magento-Store-Code", "Magento-Store-View-Code")],
          [f"{prefix}_guatemala_w", f"{prefix}_guatemala_s", f"{prefix}_guatemala_sv"])
    check(f"{key}: frase", body["phrase"], "tv 65")
    check(f"{key}: solo visibles y en stock", body["filter"], [
        {"attribute": "visibility", "in": ["Search", "Catalog, Search"]},
        {"attribute": "inStock", "eq": "true"},
    ])

# --- fallas: se reportan, no pasan por "sin resultados" ni se cachean ---------
res = fetch("curacao", lambda r: httpx.Response(200, json={"errors": [{"message": "x"}]}),
            query="error", use_cache=True)
check("error de GraphQL", (res.ok, res.error), (False, "la API respondió con error"))
check("el error no queda en caché", fetch("curacao", ok, query="error", use_cache=True).ok, True)

broken = {"data": {"productSearch": {"items": [{"productView": {"name": "x"}}]}}}
res = fetch("curacao", lambda r: httpx.Response(200, json=broken), query="roto", use_cache=True)
check("cambio de formato", (res.ok, res.error), (False, "formato inesperado"))
check("el formato roto no queda en caché", fetch("curacao", ok, query="roto", use_cache=True).ok, True)

empty = {"data": {"productSearch": {"items": []}}}
res = fetch("curacao", lambda r: httpx.Response(200, json=empty))
check("sin coincidencias no es falla", (res.ok, res.products), (True, []))

res = fetch("curacao", lambda r: httpx.Response(403, json={}))
check("HTTP 403", (res.ok, res.error), (False, "HTTP 403"))


def slow(request):
    raise httpx.ReadTimeout("lento", request=request)


check("timeout", fetch("curacao", slow).error, "timeout")

requests.clear()
fetch("curacao", ok, query="cache", use_cache=True)
fetch("curacao", ok, query="cache", use_cache=True)
check("segunda búsqueda sale del caché", len(requests), 1)

# --- la búsqueda del sitio usa este camino y no el catálogo --------------------
called = []


async def fake_adobe(client, store, query, **kw):
    called.append(store.key)
    return vtex.StoreResult(store, [], ok=True)


scraper.fetch_adobe = fake_adobe
asyncio.run(vtex.search_all([STORES["curacao"], STORES["radioshack"]], "tv", timeout=5,
                            ttl_seconds=60, use_cache=False))
check("las dos van en vivo", sorted(set(called)), ["curacao", "radioshack"])
check("quedan en catálogo", catalog_search.CATALOG_STORES, {"steren", "epa"})

if failures:
    print(f"{len(failures)} fallos:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
