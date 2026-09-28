"""Enumerador de Novex (Doofinder) sobre fixtures sintéticas. Sin red.

Se ejecuta sin dependencias:  python tests/test_ingest_novex.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from gt_compare.ingest.http import PoliteClient  # noqa: E402
from gt_compare.ingest.novex import WINDOW, enumerate_novex, record_from_result  # noqa: E402
from gt_compare.ingest.types import EnumerationStats  # noqa: E402
from gt_compare.stores import load_stores  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "novex"
STORE = {s.key: s for s in load_stores()}["novex"]
PAGE = json.loads((FIX / "search_page.json").read_text())

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


async def _nosleep(_):
    return None


def run(handler, limit=0):
    async def go():
        stats = EnumerationStats()
        client = PoliteClient("novex", min_interval=0, sleep=_nosleep,
                              transport=httpx.MockTransport(handler))
        async with client:
            recs = [r async for r in enumerate_novex(STORE, client, stats, limit=limit)]
        return recs, stats
    return asyncio.run(go())


# --- un resultado de Doofinder -------------------------------------------
promo, plain, oos = (record_from_result(STORE, r) for r in PAGE["results"][:3])
check("id como store_sku", promo.store_sku, "100101")
check("precio con descuento", (promo.price, promo.list_price), (1080.0, 1200.0))
check("sin descuento no hay list_price", (plain.price, plain.list_price), (80.0, None))
check("marca", promo.brand, "Acme")
check("categoría", promo.store_category,
      "Herramientas manuales > organización de herramientas > bolsas para herramientas arnes")
check("disponible", (promo.available, oos.available), (1, 0))
check("etiqueta de promoción", promo.promo_text, "*Exclusivo online")
check("sin EAN en Doofinder", promo.ean, None)
check("EAN si viene", record_from_result(STORE, dict(PAGE["results"][1], gtin="1234567000046")).ean,
      "1234567000046")
check("url", promo.url, "https://www.novex.com.gt/producto/100101/Mochila-demo-para-herramientas.html")
check("stock en extra", promo.extra.get("stock"), "Queda(n) 5 en stock")
check("sin precio se descarta",
      record_from_result(STORE, dict(PAGE["results"][1], best_price=None, sale_price=None, price=None)),
      None)


# --- partición por precio: un rango grande se divide ----------------------
calls = []


def handler(request: httpx.Request) -> httpx.Response:
    q = request.url.params
    calls.append(dict(q))
    lo = q.get("filter[best_price][gte]")
    hi = q.get("filter[best_price][lt]")
    body = dict(PAGE, results=[], total_found=0)
    if lo is None:                      # búsqueda vacía: total declarado
        body.update(total_found=3)
    elif (lo, hi) == ("50.00", "75.00"):  # demasiado grande: hay que partir
        body.update(total_found=WINDOW + 1)
    elif (lo, hi) == ("50.00", "62.50"):
        body.update(total_found=2, results=PAGE["results"][1:2] + PAGE["results"][1:2])
    elif (lo, hi) == ("1000.00", "2500.00"):
        body.update(total_found=1, results=PAGE["results"][:1])
    return httpx.Response(200, json=body)


recs, stats = run(handler)
check("registros deduplicados", [r.store_sku for r in recs], ["100202", "100101"])
check("bisección 50-75", any(c.get("filter[best_price][lt]") == "62.50" for c in calls), True)
check("ordena por id", all(c.get("sort[0][id]") == "asc" for c in calls), True)
check("nota de cobertura", "2 cortes con 3 resultados alcanzables; Doofinder declara 3" in stats.coverage_note, True)
check("páginas contadas", stats.pages, len(calls))

recs, stats = run(handler, limit=1)
check("límite", len(recs), 1)


def always_down(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403)


recs, stats = run(always_down)
check("tienda bloqueada no revienta", (recs, "cortada" in stats.coverage_note), ([], True))

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("ingest novex: todos los casos OK")
