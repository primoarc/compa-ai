"""Enumerador PriceSmart (Bloomreach) contra un fixture sintético, sin red.

Se ejecuta sin dependencias:  python tests/test_ingest_pricesmart.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare.ingest import pricesmart  # noqa: E402
from gt_compare.ingest.http import PoliteClient  # noqa: E402
from gt_compare.ingest.types import EnumerationStats  # noqa: E402
from gt_compare.stores import Store  # noqa: E402

FX = json.loads((Path(__file__).parent / "fixtures" / "pricesmart" / "catalog.json").read_text())
DOCS = {d["pid"]: d for d in FX["docs"]}
STORE = Store("pricesmart", "PriceSmart", "www.pricesmart.com", kind="pricesmart")

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


# --- parseo de un doc -------------------------------------------------------
rec = pricesmart.record_from_doc(STORE, DOCS["10005"], "Salud y belleza")
check("precio club en centavos -> quetzales", rec.price, 120.0)
check("precio sin ahorro (texto en quetzales) como list_price", rec.list_price, 150.0)
check("store_sku = pid", rec.store_sku, "10005")
check("url como scraper.py", rec.url,
      "https://www.pricesmart.com/es-gt/producto/acme-producto-de-higiene-demo-96-unidades-10005/10005")
check("marca", rec.brand, "Acme")
check("categoría", rec.store_category, "Salud y belleza")
check("disponible", rec.available, 1)
check("first_sku", rec.first_sku, True)
check("sin GTIN", rec.ean, None)

check("sin precio del club -> se descarta", pricesmart.record_from_doc(STORE, DOCS["100040"]), None)
check("out of stock -> 0", pricesmart.record_from_doc(STORE, DOCS["100010"]).available, 0)
check("availability false -> 0", pricesmart.record_from_doc(STORE, DOCS["100020"]).available, 0)
check("sin precio de lista si no hay ahorro", pricesmart.record_from_doc(STORE, DOCS["100030"]).list_price, None)

gorra = pricesmart.record_from_doc(STORE, DOCS["100060"])
check("precio desde la variante", gorra.price, 60.0)
check("varios GTIN -> sin ean", gorra.ean, None)
check("varios GTIN en extra", gorra.extra.get("gtins"), ["1234567000053", "1234567000060", "1234567000077"])
check("un GTIN de 11 dígitos -> UPC con cero", pricesmart.record_from_doc(STORE, DOCS["100070"]).ean, "0012345670008")


# --- enumeración completa con transporte simulado ---------------------------
def handler(seen_queries):
    def respond(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        seen_queries.append((p["search_type"], p["q"], int(p["start"])))
        if p["search_type"] == "keyword" and p["rows"] == "1":
            return httpx.Response(200, json=FX["head"])
        pids = sorted(DOCS) if p["q"] == "*" else FX["categories"].get(p["q"], [])
        start, rows = int(p["start"]), int(p["rows"])
        page = [DOCS[pid] for pid in pids[start:start + rows]]
        return httpx.Response(200, json={"response": {"numFound": len(pids), "start": start, "docs": page}})
    return respond


async def _nosleep(_):
    return None


async def run(limit=0):
    queries = []
    stats = EnumerationStats()
    client = PoliteClient("pricesmart", transport=httpx.MockTransport(handler(queries)), sleep=_nosleep)
    async with client:
        recs = [r async for r in pricesmart.enumerate_pricesmart(STORE, client, stats, limit=limit)]
    return recs, stats, queries


pricesmart.ROWS = 2  # fuerza varias páginas con el fixture chico
recs, stats, queries = asyncio.run(run())
by_sku = {r.store_sku: r for r in recs}
check("un registro por pid con precio", sorted(by_sku), sorted(p for p in DOCS if p != "100040"))
check("sin duplicados", len(recs), len(by_sku))
check("categoría de la primera raíz que lo trae", by_sku["100020"].store_category, "Alimentos")
check("categoría Moda", by_sku["100060"].store_category, "Moda y accesorios")
check("sin categoría raíz -> None", by_sku["100030"].store_category, None)
check("no consulta subcategorías", any(q == "G10D33005" for _, q, _ in queries), False)
check("paginó la categoría grande", [s for t, q, s in queries if q == "G10D03"], [0, 2])
check("stats.records", stats.records, 6)
check("stats.pages = peticiones", stats.pages, len(queries))
check("cobertura completa", stats.coverage_note.startswith("catálogo completo"), True)
check("nota con vistos/total", "7/7 pid vistos (1 sin precio" in stats.coverage_note, True)

recs, stats, _ = asyncio.run(run(limit=3))
check("respeta limit", len(recs), 3)
check("nota parcial con limit", stats.coverage_note.startswith("parcial"), True)

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("ingest pricesmart: todos los casos OK")
