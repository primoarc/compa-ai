"""Una corrida parcial no deja precios viejos como ofertas vigentes.
Ejecutar:  python tests/test_freshness.py"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from gt_compare import db as dbmod, detector, pages  # noqa: E402
from gt_compare.history import Observation, apply_observations, today_utc  # noqa: E402
from gt_compare.ingest import runner  # noqa: E402
from gt_compare.ingest.types import ProductRecord  # noqa: E402
from gt_compare.products import upsert_products  # noqa: E402
from gt_compare.stores import Store  # noqa: E402
from gt_compare.web import app  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


TODAY = today_utc()


def d(offset):
    return (date.fromisoformat(TODAY) + timedelta(days=offset)).isoformat()


mem = dbmod.open_db(":memory:")
pages.get_db = lambda: mem  # type: ignore[assignment]
STORE = Store("curacao", "La Curacao", "www.lacuracaonline.com", kind="magento")


def rec(sku, price):
    return ProductRecord("curacao", sku, f"https://x/{sku}", f"Televisor demo {sku}", price)


# 40 días a Q5.000 y hace 3 días bajan a Q3.000 los dos (corrida completa).
ids = {}
for sku in ("fresco", "viejo"):
    ids[sku] = upsert_products(mem, [rec(sku, 3000.0)], d(-3))[sku]
    for off in range(-40, -3):
        apply_observations(mem, d(off), [Observation(ids[sku], 5000.0, None, 1)], None)
    apply_observations(mem, d(-3), [Observation(ids[sku], 3000.0, None, 1)], None)
asyncio.run(detector.run(mem, d(-3)))
check("hace 3 días los dos eran oferta",
      sorted(r["product_id"] for r in mem.query("SELECT product_id FROM deals WHERE detected_on=?", (d(-3),))),
      sorted(ids.values()))
old_deal = mem.query_one("SELECT id FROM deals WHERE product_id=?", (ids["viejo"],))["id"]
mem.execute("INSERT INTO daily_pick (day, deal_id, chosen_at) VALUES (?, ?, 'x')", (d(-3), old_deal))

client = TestClient(app)
# Sin corridas desde entonces: nada de hace 3 días se muestra como vigente.
check("feed viejo no se muestra", "Televisor demo" in client.get("/ofertas").text, False)
check("oferta del día vieja no redirige", client.get("/oferta-del-dia", follow_redirects=False).status_code, 200)


# Hoy: corrida parcial de la tienda que solo alcanza a ver "fresco".
async def partial_enum(store, client, stats, *, limit=0):
    stats.partial = True
    stats.coverage_note = "parcial: 1 de 2 categorías"
    stats.records = 1
    yield rec("fresco", 3000.0)


result = asyncio.run(runner.run_store(mem, STORE, day=TODAY, enumerator=partial_enum))
check("la corrida queda parcial", result.status, "partial")
out = asyncio.run(detector.run(mem, TODAY))
today_deals = {r["product_id"] for r in mem.query("SELECT product_id FROM deals WHERE detected_on=?", (TODAY,))}
check("detector: solo el producto visto hoy", today_deals, {ids["fresco"]})

r = client.get("/ofertas")
check("ofertas: el visto hoy sí", "Televisor demo fresco" in r.text, True)
check("ofertas: el no refrescado no", "Televisor demo viejo" in r.text, False)
check("oferta del día sin refrescar no redirige", client.get("/oferta-del-dia", follow_redirects=False).status_code, 200)

r = client.get(f"/p/{ids['viejo']}")
check("ficha vieja: sin badge", 'class="badge' in r.text, False)
check("ficha vieja: sin % de oferta", "% menos" in r.text, False)
check("ficha vieja: dice cuándo se vio", f"Último precio visto el {d(-3)}" in r.text, True)
r = client.get(f"/p/{ids['fresco']}")
check("ficha fresca: con % de oferta", "40% menos" in r.text, True)

# Visto hace 2 días todavía cuenta; hace 3 ya no.
check("límite de 2 días", (pages._is_fresh([{"end_day": d(-2)}], TODAY), pages._is_fresh([{"end_day": d(-3)}], TODAY)),
      (True, False))

# --- "actualizado hace X" en las tiendas que responden desde el catálogo ------
from datetime import datetime, timezone  # noqa: E402

from gt_compare import catalog_search  # noqa: E402

run_at = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)
now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
check("visto en la última corrida: la edad de la corrida", catalog_search.seen_age("2026-09-29", run_at, now), 7200)
check("no alcanzado por la última corrida: días enteros más",
      catalog_search.seen_age("2026-09-27", run_at, now), 7200 + 2 * 86400)

found = catalog_search.search(STORE, "televisor demo", db=mem)
ages = {p.name: p.seen_age for p in found.products}
check("el visto hoy: menos de una hora", ages["Televisor demo fresco"] < 3600, True)
check("el no refrescado: 3 días o más", ages["Televisor demo viejo"] >= 3 * 86400, True)


# Las tiendas de catálogo se marcan como vistas todos los días; las demás, día por medio.
def one(sku):
    async def enum(store, client, stats, *, limit=0):
        stats.records = 1
        yield ProductRecord(store.key, sku, f"https://x/{sku}", f"Licuadora {sku}", 500.0)
    return enum


def end_day(store_key, sku):
    return mem.query_one("""SELECT MAX(h.end_day) AS e FROM price_history h JOIN products p ON p.id=h.product_id
                            WHERE p.store_key=? AND p.store_sku=?""", (store_key, sku))["e"]


SIMAN = Store("siman", "Siman", "gt.siman.com", kind="vtex")
for store in (STORE, SIMAN):
    for day in (d(-1), TODAY):
        asyncio.run(runner.run_store(mem, store, day=day, enumerator=one("diario")))
check("catálogo: visto hoy aunque no cambió", end_day("curacao", "diario"), TODAY)
check("otras tiendas: día por medio", end_day("siman", "diario"), d(-1))

if failures:
    print(f"{len(failures)} fallos:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
