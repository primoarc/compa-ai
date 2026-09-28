"""Enumerador Intelaf sin red.

Se ejecuta sin dependencias:  python tests/test_ingest_intelaf.py

Los fixtures son respuestas reales de la API (búsquedas "laptop" y "mouse")
recortadas a pocos productos y dos sucursales de existencia.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from gt_compare.ingest.http import PoliteClient, StoreBlocked  # noqa: E402
from gt_compare.ingest.intelaf import (  # noqa: E402
    PAGE_SIZE, category_names, enumerate_intelaf, parse_page,
)
from gt_compare.ingest.types import EnumerationStats  # noqa: E402
from gt_compare.stores import Store  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures" / "intelaf"
STORE = Store(key="intelaf", name="Intelaf", domain="www.intelaf.com", kind="intelaf",
              enabled=True, search_path=None)
LAPTOP = json.loads((FX / "busqueda_laptop.json").read_text())
MOUSE = json.loads((FX / "busqueda_mouse.json").read_text())

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


# --- parseo -----------------------------------------------------------------
recs, total = parse_page(STORE, LAPTOP, category_names(LAPTOP))
by = {r.store_sku: r for r in recs}
check("total declarado", total, 22)
check("skus", sorted(by), ["DEMO-DOCK-300", "DEMO-KIT-100", "DEMO-NB-200"])
k = by["DEMO-KIT-100"]
check("precio con descuento", (k.price, k.list_price), (90.0, 100.0))
check("existencia + en tránsito", k.available, 7)
check("marca", k.brand, "ACME")
check("categoría con nombre de la faceta", k.store_category, "Accesorios Demo")
check("área en extra", k.extra, {"area": "CEL-ACC"})
check("promo con vencimiento", k.promo_text, "Promo ACME (hasta 30/09/2026)")
check("url", k.url, "https://www.intelaf.com/producto/DEMO-KIT-100")
check("imagen", k.image, "https://example.com/img/DEMO-KIT-100.jpg")
check("sin EAN", k.ean, None)
check("fecha 01/01/0001 no se muestra", by["DEMO-NB-200"].promo_text, "Beneficio Efectivo")
check("solo en tránsito cuenta 1", by["DEMO-DOCK-300"].available, 1)
nb = by["DEMO-NB-200"]
check("beneficio efectivo: precio comparable es el de tarjeta", (nb.price, nb.cash_price, nb.list_price), (9000.0, 8500.0, None))
check("promo para todos los medios de pago no tiene precio de contado", k.cash_price, None)

recs, _ = parse_page(STORE, MOUSE, category_names(MOUSE))
by = {r.store_sku: r for r in recs}
check("agotado", by["DEMO-MOU-400"].available, 0)
check("categoría mouse", by["DEMO-MOU-500"].store_category, "Mouse Demo")

# Casos armados a partir del fixture.
base = MOUSE["Response"]["Productos"][1]
sin_desc = dict(base, PrecioDescuento=0.0)
sin_codigo = dict(base, Codigo="")
sin_precio = dict(base, Codigo="X-0", PrecioNormal=0.0, PrecioDescuento=0.0)
recs, _ = parse_page(STORE, {"Response": {"Productos": [sin_desc, sin_codigo, sin_precio]}})
check("sin código o sin precio se omite", [r.store_sku for r in recs], ["DEMO-MOU-500"])
check("sin descuento: precio normal, sin lista ni promo",
      (recs[0].price, recs[0].list_price, recs[0].promo_text), (80.0, None, None))
check("sin faceta usa el código de área", recs[0].store_category, "MOUSE-OPT-ALA")


# --- recorrido --------------------------------------------------------------
def clones(n, prefix):
    item = LAPTOP["Response"]["Productos"][0]
    return [dict(copy.deepcopy(item), Codigo=f"{prefix}-{i}") for i in range(n)]


async def nosleep(_):
    return None


async def run(handler, limit=0):
    stats = EnumerationStats()
    async with PoliteClient("intelaf", min_interval=0, sleep=nosleep,
                            transport=httpx.MockTransport(handler)) as client:
        recs = [r async for r in enumerate_intelaf(STORE, client, stats, limit=limit)]
    return recs, stats


calls = []


def handler(request):
    body = json.loads(request.content)
    calls.append((body["Query"], body["Pagina"], body["CantidadMaxima"]))
    if body["Query"] == "-":
        if body["Pagina"] == 1:
            prods = clones(PAGE_SIZE, "P")
        else:
            prods = copy.deepcopy(LAPTOP["Response"]["Productos"])
        resp = {"CantidadProductos": PAGE_SIZE + 3, "Productos": prods,
                "Categorias": LAPTOP["Response"]["Categorias"]}
    else:
        # "o" repite los de la página 2 y agrega los de mouse
        prods = copy.deepcopy(LAPTOP["Response"]["Productos"] + MOUSE["Response"]["Productos"])
        resp = {"CantidadProductos": 5, "Productos": prods}
    return httpx.Response(200, json={"state": {"Code": 200}, "Response": resp})


recs, stats = asyncio.run(run(handler))
check("peticiones", calls, [("-", 1, 100), ("-", 2, 100), ("o", 1, 100)])
check("registros deduplicados", (len(recs), len({r.store_sku for r in recs})), (105, 105))
check("stats", (stats.pages, stats.records, stats.errors), (3, 105, 0))
check("nota", stats.coverage_note,
      "casi completo por búsqueda amplia ('-', 'o'): 103 declarados, 105 SKUs únicos")

calls.clear()
recs, stats = asyncio.run(run(handler, limit=7))
check("límite", (len(recs), stats.records, len(calls)), (7, 7, 1))
check("nota con límite", stats.coverage_note, "parcial: límite de 7 registros")


def error_handler(request):
    body = json.loads(request.content)
    if body["Query"] == "-":
        return httpx.Response(200, json={"state": {"Code": 500, "Message": "x"}, "Response": {}})
    return httpx.Response(200, json=MOUSE)


recs, stats = asyncio.run(run(error_handler))
check("error de la API no corta la siguiente búsqueda",
      ([r.store_sku for r in recs], stats.errors), (["DEMO-MOU-400", "DEMO-MOU-500"], 1))


class Blocked:
    async def post(self, *a, **kw):
        raise StoreBlocked("intelaf")


async def run_blocked():
    stats = EnumerationStats()
    recs = [r async for r in enumerate_intelaf(STORE, Blocked(), stats)]
    return recs, stats


recs, stats = asyncio.run(run_blocked())
check("tienda bloqueada", (recs, stats.errors), ([], 1))
check("nota bloqueada", stats.coverage_note.endswith(" (cortada por errores seguidos)"), True)

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("ingest intelaf: todos los casos OK")
