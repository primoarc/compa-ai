"""Enumerador Kemik sin red.

Se ejecuta sin dependencias:  python tests/test_ingest_kemik.py

`fixtures/kemik/categoria.html` es sintética con la forma de
/computadoras-accesorios: 3 productos inventados (payload RSC + JSON-LD). El
recorrido del árbol se prueba con páginas sintéticas del mismo formato.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from gt_compare.ingest.http import PoliteClient, StoreBlocked  # noqa: E402
from gt_compare.ingest.kemik import enumerate_kemik, navigator, parse_products  # noqa: E402
from gt_compare.ingest.types import EnumerationStats  # noqa: E402
from gt_compare.stores import Store  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures" / "kemik"
STORE = Store(key="kemik", name="Kemik", domain="www.kemik.gt", kind="kemik",
              enabled=True, search_path=None)
HTML = (FX / "categoria.html").read_text()

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


# --- parseo del fixture de categoría -----------------------------------------------
count, kids = navigator(HTML)
check("conteo de la categoría", count, 3614)
check("hijas", kids[:3], [("accesorios-para-computadoras", 2404),
                          ("componentes-de-computadora", 769), ("monitores-computadora", 228)])

recs = parse_products(STORE, HTML)
by = {r.store_sku: r for r in recs}
check("skus", sorted(by), ["D0000000A1", "D0000000B2", "D0000000C3"])
a = by["D0000000A1"]
check("precio y precio normal", (a.price, a.list_price), (35.0, 45.0))
check("marca", a.brand, "Marca Demo")
check("categoría", a.store_category, "Adaptadores de Video")
check("disponible (IN_ERP_STOCK)", a.available, 1)
check("url", a.url, "https://www.kemik.gt/adaptador-demo-hdmi-a-vga-15cm-negro")
check("imagen del JSON-LD", a.image,
      "https://static.kemikcdn.com/2026/01/demo-adaptador-hdmi-300x300.jpg")
check("nombre", a.name, "Marca Demo Adaptador HDMI a VGA 15cm Negro")
b = by["D0000000B2"]
check("sin marca ni oferta", (b.brand, b.list_price, b.price), (None, None, 50.0))
c = by["D0000000C3"]
check("oferta flash e internacional", (c.promo_text, c.extra["international"]),
      ("Oferta flash", True))

# Respaldo JSON-LD: sin payload RSC.
ld_only = re.sub(r"<script>self\.__next_f.*?</script>", "", HTML, flags=re.S)
recs = parse_products(STORE, ld_only)
by = {r.store_sku: r for r in recs}
check("respaldo JSON-LD", sorted(by), ["D0000000A1", "D0000000B2", "D0000000C3"])
check("respaldo JSON-LD campos", (by["D0000000A1"].price, by["D0000000A1"].brand,
                                  by["D0000000A1"].list_price), (35.0, "Marca Demo", None))
check("sin navegador", navigator(ld_only), (None, []))

# Respaldo regex del scraper (tarjeta mínima con el formato que espera).
card = ('<a title="Mouse Inalambrico" class="x" href="/mouse-inalambrico-negro-marca-generica">'
        '<img src="https://static.kemikcdn.com/m.jpg">'
        '<div data-component="Price"><div>Q1,469</div></div></a>')
recs = parse_products(STORE, card)
check("respaldo regex", [(r.store_sku, r.price, r.available) for r in recs],
      [("mouse-inalambrico-negro-marca-generica", 1469.0, 1)])


# --- recorrido con páginas sintéticas --------------------------------------
def page(count, children=(), skus=(), with_nav=True):
    nav = {"current": {"value": {"slug": "x"}, "count": count},
           "children": [{"value": {"slug": s}, "count": n} for s, n in children]}
    prods = [{"public_sku": s, "slug": f"p-{s.lower()}", "title": f"Producto {s}",
              "price": 10, "regular_price": 12, "stock_status": "IN_STOCK",
              "status": "PUBLISHED", "brand": {"name": "Marca"},
              "main_category": {"name": "Cat"}, "main_image": {"name": f"{s}.jpg"}}
             for s in skus]
    rsc = ('7:["$","$L49",null,{"categoryNavigator":' + json.dumps(nav) + '}]\n' if with_nav else ""
           ) + '53:["$","$L57",null,{"children":' + json.dumps(prods) + '}]\n'
    return ('<html><body><script>self.__next_f.push([1,' + json.dumps(rsc)
            + '])</script></body></html>')


def skus(prefix, n):
    return [f"{prefix}{i}" for i in range(n)]


SITE = {
    "/search?query=": page(0, [("grande", 5), ("chica", 5), ("sinnav", 5), ("uncategorized", 3)]),
    # 1500 > 1000: baja a la hija con productos y no pagina la madre
    "/grande": page(1500, [("hija", 50), ("vacia", 0)], skus("G", 40)),
    "/hija": page(50, [], skus("H", 40)),
    "/hija?page=2": page(50, [], skus("H", 38) + skus("G", 2) + ["H40", "H41"]),
    # 90 productos: 3 páginas, la última corta
    "/chica": page(90, [], skus("C", 40)),
    "/chica?page=2": page(90, [], skus("C", 80)[40:]),
    "/chica?page=3": page(90, [], skus("C", 90)[80:]),
    # sin navegador: pagina hasta una página corta
    "/sinnav": page(0, [], skus("S", 40), with_nav=False),
    "/sinnav?page=2": page(0, [], skus("S", 45)[40:], with_nav=False),
}


async def nosleep(_):
    return None


async def run(site, limit=0, max_pages=600):
    calls = []

    def handler(request):
        path = request.url.raw_path.decode()
        calls.append(path)
        return httpx.Response(200, text=site[path]) if path in site else httpx.Response(404)

    stats = EnumerationStats()
    async with PoliteClient("kemik", min_interval=0, sleep=nosleep,
                            transport=httpx.MockTransport(handler)) as client:
        recs = [r async for r in enumerate_kemik(STORE, client, stats, limit=limit,
                                                 max_pages=max_pages)]
    return recs, stats, calls


recs, stats, calls = asyncio.run(run(SITE))
check("páginas pedidas", sorted(calls), sorted(SITE))
check("sin duplicados", len(recs), len({r.store_sku for r in recs}))
check("registros", (len(recs), stats.records), (40 + 42 + 90 + 45, 217))
check("stats", (stats.pages, stats.errors), (9, 0))
check("imagen desde el CDN", recs[0].image.startswith("https://static.kemikcdn.com/"), True)
check("nota completa", stats.coverage_note,
      "categorías: 4 visitadas, 9 páginas, 1590 productos declarados en raíces, "
      "217 SKUs únicos; ~1450 fuera por el tope de 25 páginas; árbol completo")

recs, stats, calls = asyncio.run(run(SITE, limit=45))
check("límite", (len(recs), stats.records), (45, 45))
check("nota con límite", stats.coverage_note.endswith("parcial: límite de 45 registros"), True)

recs, stats, calls = asyncio.run(run(SITE, max_pages=3))
check("presupuesto: peticiones", len(calls), 3)
check("presupuesto: nota", "presupuesto de 3 páginas agotado" in stats.coverage_note, True)
check("presupuesto agotado: parcial", stats.partial, True)
recs, stats, calls = asyncio.run(run(SITE))
check("árbol completo: no es parcial", stats.partial, False)

# Una página que da 404 cuenta como error y el recorrido sigue.
broken = {k: v for k, v in SITE.items() if k != "/chica?page=2"}
recs, stats, calls = asyncio.run(run(broken))
check("404 cuenta error y sigue", (stats.errors, len(recs)), (1, 40 + 42 + 40 + 45))

# Si la búsqueda vacía falla, usa la lista fija de raíces.
no_search = {k: v for k, v in SITE.items() if k != "/search?query="}
recs, stats, calls = asyncio.run(run(no_search, max_pages=4))
check("raíces de respaldo", calls[0] == "/search?query=" and len(calls) == 4, True)


class Blocked:
    async def get(self, *a, **kw):
        raise StoreBlocked("kemik")


async def run_blocked():
    stats = EnumerationStats()
    recs = [r async for r in enumerate_kemik(STORE, Blocked(), stats)]
    return recs, stats


recs, stats = asyncio.run(run_blocked())
check("tienda bloqueada", (recs, stats.errors), ([], 1))
check("nota bloqueada", stats.coverage_note.endswith(" (cortada por errores seguidos)"), True)

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("ingest kemik: todos los casos OK")
