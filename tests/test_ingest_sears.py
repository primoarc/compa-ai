"""Enumerador de Sears sobre fixtures sintéticas. Sin red.

Se ejecuta sin dependencias:  python tests/test_ingest_sears.py
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
from gt_compare.ingest.sears import (  # noqa: E402
    _gtin, enumerate_sears, record_from_api, records_from_html,
)
from gt_compare.ingest.types import EnumerationStats  # noqa: E402
from gt_compare.stores import load_stores  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sears"
STORE = {s.key: s for s in load_stores()}["sears"]
API = json.loads((FIX / "store_api_page.json").read_text())
HTML = (FIX / "shop_page.html").read_text()

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


async def _nosleep(_):
    return None


def run(handler, limit=0):
    async def go():
        stats = EnumerationStats()
        client = PoliteClient("sears", min_interval=0, sleep=_nosleep,
                              transport=httpx.MockTransport(handler))
        async with client:
            recs = [r async for r in enumerate_sears(STORE, client, stats, limit=limit)]
        return recs, stats
    return asyncio.run(go())


# --- Store API: precio en unidades menores, rebaja, agotado ---------------
regular, sale, oos, var = (record_from_api(STORE, p) for p in API[:4])
check("id como store_sku", regular.store_sku, "900101")
check("precio en quetzales", regular.price, 60.0)
check("sin rebaja no hay list_price", regular.list_price, None)
check("categoría", regular.store_category, "Unisex")
check("sku interno de 6 dígitos no es EAN", regular.ean, None)
check("imagen", regular.image, "https://sears.com.gt/wp-content/uploads/500101.jpg")
check("url", regular.url, "https://sears.com.gt/producto/juego-de-cartas-demo-acme/")
check("rebaja: precio", sale.price, 360.0)
check("rebaja: list_price", sale.list_price, 400.0)
check("agotado", (oos.available, regular.available), (0, 1))
check("variable usa el precio del producto", (var.price, var.list_price), (220.0, 260.0))
check("GTIN de 13 dígitos", _gtin("1234567000084"), "1234567000084")
check("GTIN no acepta 6 ni 10 dígitos", (_gtin("500101"), _gtin("1234567890")), (None, None))
sin_precio = dict(API[0], prices=dict(API[0]["prices"], price=""))
check("sin precio se descarta", record_from_api(STORE, sin_precio), None)
con_marca = dict(API[0], attributes=[{"name": "Marca", "terms": [{"name": "Acme"}]}])
check("marca desde atributo", record_from_api(STORE, con_marca).brand, "Acme")

# --- HTML: id del post y rebaja ------------------------------------------
html_recs = records_from_html(STORE, HTML)
check("HTML: productos", len(html_recs), 3)
check("HTML: id del post (= id de la API)", html_recs[0].store_sku, "900201")
check("HTML: rebaja", (html_recs[0].price, html_recs[0].list_price), (405.0, 450.0))


# --- enumerador completo con transporte simulado --------------------------
def api_handler(request: httpx.Request) -> httpx.Response:
    page = int(request.url.params.get("page", "1"))
    if "/wp-json/" not in request.url.path:
        return httpx.Response(500)
    body = API if page == 1 else []
    return httpx.Response(200, json=body, headers={"X-WP-Total": "4", "X-WP-TotalPages": "1"})


recs, stats = run(api_handler)
check("API: deduplica el repetido", [r.store_sku for r in recs], ["900101", "900102", "900103", "12001"])
check("API: páginas y registros", (stats.pages, stats.records), (1, 4))
check("API: nota de cobertura", "catálogo completo por Store API: 4" in stats.coverage_note, True)

recs, stats = run(api_handler, limit=2)
check("límite", len(recs), 2)


def blocked_api(request: httpx.Request) -> httpx.Response:
    if "/wp-json/" in request.url.path:
        return httpx.Response(403)
    page = request.url.path.strip("/").split("/")[-1]
    return httpx.Response(200, text=HTML) if page == "1" else httpx.Response(404)


recs, stats = run(blocked_api)
check("respaldo HTML", (len(recs), stats.pages), (3, 3))
check("respaldo HTML: nota", stats.coverage_note.startswith("listado HTML"), True)

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("ingest sears: todos los casos OK")
