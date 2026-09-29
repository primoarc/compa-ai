"""Parseo de listados Magento para gt_compare.ingest.magento, sin red.

Se ejecuta sin dependencias:  python tests/test_ingest_magento.py

Los fixtures son sintéticos con el marcado de Steren, La Curacao y EPA
(productos, precios e ids inventados).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare.ingest.magento import (  # noqa: E402
    _base_path, _declared_total, _page_url, _RE_NEXT, parse_listing,
)
from gt_compare.stores import Store  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "magento"

STEREN = Store("steren", "Steren", "www.steren.com.gt", kind="magento",
               search_path="/catalogsearch/result/?q=")
CURACAO = Store("curacao", "La Curacao", "www.lacuracaonline.com", kind="magento",
                search_path="/guatemala/search/")
EPA = Store("epa", "EPA", "gt.epaenlinea.com", kind="magento",
            search_path="/catalogsearch/result/?q=")

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


def summary(recs):
    return [(r.store_sku, r.price, r.list_price, r.available) for r in recs]


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


# --- Steren: oldPrice va antes que finalPrice; un producto agotado ----------
html = load("steren_listing.html")
recs = parse_listing(STEREN, html, "Audífonos")
check("steren: id, precio final, precio lista, disponibilidad", summary(recs), [
    ("50101", 290.0, 350.0, 1),
    ("50102", 50.0, None, 0),
    ("50103", 150.0, None, 1),
])
first = recs[0]
check("steren: url absoluta", first.url,
      "https://www.steren.com.gt/audifonos-demo-gamer-usb-7-1.html")
check("steren: nombre sin etiquetas", first.name,
      "Audífonos Demo Gamer USB 7.1 con ecualizador")
check("steren: sku de tienda en extra", first.extra.get("sku"), "DEM-100")
check("steren: categoría", first.store_category, "Audífonos")
check("steren: first_sku", first.first_sku, True)
check("steren: imagen", bool(first.image and first.image.endswith(".jpg")), True)
check("steren: sin paginación siguiente", bool(_RE_NEXT.search(html)), False)

# --- La Curacao: precio especial vs regular; widget de comparar ignorado ----
html = load("curacao_listing.html")
recs = parse_listing(CURACAO, html)
check("curacao: registros", summary(recs), [
    ("3100001", 750.0, 850.0, 1),
    ("3100002", 9000.0, None, 1),
])
check("curacao: sku Magento en extra", recs[0].extra.get("sku"), "900000000011")
check("curacao: imagen sin &amp;", "&amp;" in (recs[0].image or ""), False)
check("curacao: total declarado", _declared_total(html), 1200)
check("curacao: hay página siguiente", bool(_RE_NEXT.search(html)), True)

# --- EPA: precio normal; "Agotado" del pie (plantilla Algolia) no cuenta ----
html = load("epa_listing.html")
recs = parse_listing(EPA, html, "Productos")
check("epa: registros", summary(recs), [
    ("700001", 5.5, None, 1),
    ("700002", 6.5, None, 1),
])
check("epa: nombre con comillas", recs[0].name, 'Arandela demo 5/16" 10 unidades')

# --- utilidades de URL ------------------------------------------------------
check("base unicomer", _base_path(CURACAO), "/guatemala/")
check("base catalogsearch", _base_path(STEREN), "/")
check("página 1 sin p", _page_url("https://x/c/a", 1), "https://x/c/a")
check("página 2", _page_url("https://x/c/a", 2), "https://x/c/a?p=2")
check("página 2 con query", _page_url("https://x/s/?q=tv", 2), "https://x/s/?q=tv&p=2")

# --- sin id Magento se usa la ruta de la URL ---------------------------------
bare = ('<ol><li class="item product product-item"><a class="product-item-link" '
        'href="/foco-led.html">Foco LED</a><span data-price-amount="25" '
        'data-price-type="finalPrice"></span></li></ol>')
recs = parse_listing(STEREN, bare)
check("fallback a ruta", [(r.store_sku, r.url, r.price) for r in recs],
      [("/foco-led.html", "https://www.steren.com.gt/foco-led.html", 25.0)])

# --- robots.txt: Steren prohíbe URLs con "?", solo se pide la página 1 ------
import asyncio  # noqa: E402

import httpx  # noqa: E402

from gt_compare.ingest.http import PoliteClient  # noqa: E402
from gt_compare.ingest.magento import enumerate_magento  # noqa: E402
from gt_compare.ingest.types import EnumerationStats  # noqa: E402

LISTING = (FIX / "steren_listing.html").read_text()


def crawl(store):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path == "/" or request.url.path == "/guatemala/":
            return httpx.Response(200, text='<a href="/audio">Audio</a><a href="/audio/demo">Demo</a>')
        return httpx.Response(200, text=LISTING + '<li class="pages-item-next"></li>')

    async def run():
        stats = EnumerationStats()
        client = PoliteClient(store.key, min_interval=0, transport=httpx.MockTransport(handler))
        async with client:
            return [r async for r in enumerate_magento(store, client, stats)], requested

    return asyncio.run(run())


recs, requested = crawl(STEREN)
check("steren: ninguna URL con ?", [u for u in requested if "?" in u], [])
check("steren: lee la primera página", bool(recs), True)
OTRA = Store("otra", "Otra", "www.otra.test", kind="magento", search_path="/catalogsearch/result/?q=")
_, requested = crawl(OTRA)
check("otras tiendas sí paginan", any("?p=2" in u for u in requested), True)

# --- La Curacao: 406 intermitente, un reintento y seguir con la siguiente ---------
import logging  # noqa: E402

CURACAO_HOME = ('<a href="https://www.lacuracaonline.com/guatemala/c/audio">A</a>'
                '<a href="https://www.lacuracaonline.com/guatemala/c/video">V</a>')
PAGE1 = (FIX / "curacao_listing.html").read_text()


def crawl_406(store, fails):
    """`fails`: {url: cuántas veces responde 406 antes de dar 200}."""
    pauses, log = [], []
    left = dict(fails)

    def handler(request):
        url = str(request.url)
        if left.get(url, 0) > 0:
            left[url] -= 1
            return httpx.Response(406, headers={"x-demo": "1", "set-cookie": "sesion=abc"})
        if request.url.path == "/guatemala/":
            return httpx.Response(200, text=CURACAO_HOME)
        if "p=2" in url:
            return httpx.Response(200, text="<ol></ol>")
        return httpx.Response(200, text=PAGE1)

    class Grab(logging.Handler):
        def emit(self, record):
            log.append(record.getMessage())

    async def fake_sleep(sec):
        pauses.append(sec)

    async def run():
        stats = EnumerationStats()
        client = PoliteClient(store.key, min_interval=0, transport=httpx.MockTransport(handler),
                              sleep=fake_sleep)
        async with client:
            recs = [r async for r in enumerate_magento(store, client, stats)]
        return recs, stats

    grab = Grab()
    logging.getLogger("gt_compare.ingest").addHandler(grab)
    try:
        recs, stats = asyncio.run(run())
    finally:
        logging.getLogger("gt_compare.ingest").removeHandler(grab)
    return recs, stats, pauses, log


AUDIO_P2 = "https://www.lacuracaonline.com/guatemala/c/audio?p=2"
_, stats, pauses, log = crawl_406(CURACAO, {AUDIO_P2: 1})
check("406 que se recupera: pausa de 20 s y sin parcial", (pauses, stats.partial), ([20.0], False))
_, stats, pauses, log = crawl_406(CURACAO, {AUDIO_P2: 2})
check("406 que persiste: sigue con la otra categoría y queda parcial",
      (pauses, stats.partial, "2 de 2" in stats.coverage_note, "incompletas por errores" in stats.coverage_note),
      ([20.0], True, True, True))
detail = [m for m in log if "primer 406" in m]
check("primer 406 con todas las cabeceras, cookies tapadas",
      (len(detail), "'x-demo': '1'" in detail[0], "abc" in detail[0], "(omitida)" in detail[0]),
      (1, True, False, True))
check("sin cabecera server se dice así", any("server=(sin cabecera) (cuerpo vacío)" in m for m in log), True)
check("cobertura sobre lo declarado", "cobertura" in stats.coverage_note, True)
VIDEO_P1 = "https://www.lacuracaonline.com/guatemala/c/video"
_, stats, _, _ = crawl_406(CURACAO, {VIDEO_P1: 2})
check("categoría sin total se avisa", "1 categorías sin total" in stats.coverage_note, True)
_, stats, pauses, _ = crawl_406(EPA, {"https://gt.epaenlinea.com/": 1})
check("otras tiendas no reintentan el 406", pauses, [])

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("ingest magento: todos los casos OK")
