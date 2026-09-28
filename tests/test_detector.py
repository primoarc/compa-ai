"""Detector de ofertas y price errors.  Ejecutar:  python tests/test_detector.py"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare import db as dbmod, detector  # noqa: E402
from gt_compare.history import Observation, apply_observations  # noqa: E402
from gt_compare.ingest.types import ProductRecord  # noqa: E402
from gt_compare.products import upsert_products  # noqa: E402

failures = []
TODAY = "2026-09-28"


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


def d(offset):
    return (date.fromisoformat(TODAY) + timedelta(days=offset)).isoformat()


def iv(a, b, price, list_price=None, available=1):
    return {"start_day": d(a), "end_day": d(b), "price": price, "list_price": list_price,
            "available": available}


def verdict(intervals, price, list_price=None, peers=()):
    product = {"cur_price": price, "cur_list_price": list_price}
    return detector.classify(detector.features_for(product, intervals, list(peers), TODAY))


stable = [iv(-60, -1, 1000.0)]

# --- una clase por caso -----------------------------------------------------
v = verdict(stable + [iv(0, 0, 820.0)], 820.0)
check("oferta: 18% bajo la mediana", v.kind, "oferta")

v = verdict(stable + [iv(0, 0, 650.0)], 650.0)
check("oferta fuerte: 35%", v.kind, "oferta_fuerte")

v = verdict(stable + [iv(0, 0, 300.0)], 300.0)
check("posible error: 70% y súbito", v.kind, "posible_error")
check("posible error: referencia del historial", (v.reference, v.reference_kind), (1000.0, "historial"))

v = verdict(stable + [iv(0, 0, 980.0)], 980.0)
check("precio normal: nada", v.kind, None)

# 55% abajo pero bajando de a poco (sin caída súbita ni otras tiendas): fuerte, no error
gradual = [iv(-60, -21, 1000.0), iv(-20, -11, 800.0), iv(-10, -1, 600.0), iv(0, 0, 450.0)]
v = verdict(gradual, 450.0)
check("caída gradual grande no es price error", v.kind, "oferta_fuerte")

# Sin historial pero otras tiendas lo confirman
v = verdict([iv(0, 0, 400.0)], 400.0, peers=[1000.0, 1050.0])
check("sin historial, confirmado por otras tiendas", (v.kind, v.reference_kind),
      ("posible_error", "otras_tiendas"))

# El ahorro en quetzales ordena: mismo %, más ahorro, más arriba
tv_like = verdict([iv(-60, -1, 10000.0), iv(0, 0, 7000.0)], 7000.0)
earrings = verdict([iv(-60, -1, 330.0), iv(0, 0, 231.0)], 231.0)
check("mismo % pero más ahorro puntúa más", tv_like.score > earrings.score, True)

# --- falsos positivos típicos ---------------------------------------------
# Vuelve a su precio normal después de un alza temporal
spike = [iv(-89, -21, 1000.0), iv(-20, -1, 1500.0), iv(0, 0, 1000.0)]
v = verdict(spike, 1000.0)
check("volver al precio normal tras un alza no es oferta", v.kind, None)

# "Antes" inflado en las dos semanas previas: descuento falso
fake = [iv(-60, -8, 1000.0, 1000.0), iv(-7, -1, 1000.0, 1600.0), iv(0, 0, 999.0, 1600.0)]
v = verdict(fake, 999.0, list_price=1600.0)
check("precio de lista inflado: descuento falso", v.kind, "descuento_falso")

# "Antes" alto de siempre y el precio no cambió: no es oferta ni descuento falso
always = [iv(-60, -1, 1000.0, 1600.0), iv(0, 0, 1000.0, 1600.0)]
v = verdict(always, 1000.0, list_price=1600.0)
check("precio de lista alto de siempre: nada", v.kind, None)

# Historial muy corto y sin otras tiendas: no se puede decir nada
short = [iv(-3, -1, 1000.0), iv(0, 0, 300.0)]
v = verdict(short, 300.0)
check("menos de 7 días de historial: nada", v.kind, None)

# Días agotado no cuentan para la mediana
oos = [iv(-60, -31, 1000.0), iv(-30, -1, 3000.0, available=0), iv(0, 0, 820.0)]
v = verdict(oos, 820.0)
check("días agotados fuera de la mediana", v.reference, 1000.0)

# Productos baratos no se publican
v = verdict([iv(-60, -1, 20.0), iv(0, 0, 5.0)], 5.0)
check("referencia bajo Q50: nada", v.kind, None)

# Otra tienda más barata de siempre no infla la caída
v = verdict(stable + [iv(0, 0, 900.0)], 900.0, peers=[850.0])
check("otras tiendas más baratas: referencia propia", v.reference_kind, "historial")


# --- corrida completa: cola de aprobación y filtro de causa -----------------
async def full_run():
    mem = dbmod.open_db(":memory:")

    def seed(sku, name, history, today_price, promo=None):
        rec = ProductRecord("siman", sku, f"https://x/{sku}", name, today_price, promo_text=promo)
        pid = upsert_products(mem, [rec], TODAY)[sku]
        for day_off, price in history:
            apply_observations(mem, d(day_off), [Observation(pid, price, None, 1)], None)
        apply_observations(mem, TODAY, [Observation(pid, today_price, None, 1)], None)
        return pid

    hist = [(o, 2000.0) for o in range(-40, 0)]
    err = seed("1", "Televisor Samsung 65 QLED", hist, 500.0)
    card = seed("2", "Laptop Lenovo IdeaPad", hist, 600.0, promo="Precio con tarjeta BI")
    usd = seed("3", "Refrigeradora LG", [(o, 2310.0) for o in range(-40, 0)], 300.0)
    deal = seed("4", "Licuadora Oster", [(o, 1000.0) for o in range(-40, 0)], 700.0)
    out = await detector.run(mem, TODAY, decider=None)
    rows = {r["product_id"]: r for r in mem.query("SELECT * FROM deals")}
    check("error real queda pendiente de aprobación", (rows[err]["kind"], rows[err]["status"], rows[err]["cause"]),
          ("posible_error", "pending", "error_real"))
    check("precio con tarjeta se descarta", (rows[card]["status"], rows[card]["cause"]),
          ("discarded", "condicion_tarjeta"))
    check("precio en dólares se descarta", (rows[usd]["status"], rows[usd]["cause"]),
          ("discarded", "moneda_unidad"))
    check("oferta fuerte se publica sin cola", (rows[deal]["kind"], rows[deal]["status"]),
          ("oferta_fuerte", "published"))
    check("conteo", out["products"], 4)

    # Una decisión del panel no se pisa al volver a correr
    mem.execute("UPDATE deals SET status='approved' WHERE product_id=?", (err,))
    await detector.run(mem, TODAY, decider=None)
    check("aprobación se conserva",
          mem.query_one("SELECT status FROM deals WHERE product_id=?", (err,))["status"], "approved")


asyncio.run(full_run())


# --- un par por EAN solo sostiene una oferta fuerte o un error si se valida ---
async def ean_validation():
    mem = dbmod.open_db(":memory:")

    def seed(store, sku, name, price):
        rec = ProductRecord(store, sku, f"https://x/{store}/{sku}", name, price)
        pid = upsert_products(mem, [rec], TODAY)[sku]
        apply_observations(mem, TODAY, [Observation(pid, price, None, 1)], None)
        return pid

    def link(a, b):
        cid = mem.execute("INSERT INTO clusters (ean, name) VALUES ('0000000000000', 'x')")
        mem.executemany("INSERT INTO product_clusters (product_id, cluster_id, method, confidence, decided_at) "
                        "VALUES (?,?,?,?,?)", [(a, cid, "ean", 0.92, TODAY), (b, cid, "ean", 0.92, TODAY)])

    ok_a = seed("siman", "1", 'Televisor Samsung 55" UN55DU7000', 1500.0)
    ok_b = seed("walmart", "2", 'Pantalla Samsung UN55DU7000 55"', 4000.0)
    bad_a = seed("siman", "3", "Licuadora Oster 600W vaso de vidrio", 150.0)
    bad_b = seed("walmart", "4", "Licuadora Ninja Professional 1000W", 2950.0)
    link(ok_a, ok_b)
    link(bad_a, bad_b)
    out = await detector.run(mem, TODAY)
    kinds = {r["product_id"]: r["kind"] for r in mem.query("SELECT product_id, kind FROM deals")}
    check("par consistente sostiene el posible error", kinds.get(ok_a), "posible_error")
    check("par contradictorio (otra marca) no sostiene nada", kinds.get(bad_a), None)
    check("se cuenta la baja de clase", out["peer_rejected_downgrades"], 1)


asyncio.run(ean_validation())

# --- qué se marca como visto todos los días ------------------------------------
from gt_compare.ingest.runner import daily_marked  # noqa: E402

m = dbmod.open_db(":memory:")
m.executemany("INSERT INTO deals (product_id, detected_on, kind, score, price, status) VALUES (?,?,?,?,?,?)", [
    (1, d(-1), "oferta", 20, 10, "published"),     # día viejo: no cuenta
    (2, TODAY, "oferta", 20, 10, "published"),     # en /ofertas
    (3, TODAY, "posible_error", 90, 10, "pending"),  # en la cola del panel
    (4, TODAY, "posible_error", 90, 10, "discarded"),
    (5, TODAY, "descuento_falso", 5, 10, "flagged"),
])
old_pick = m.execute("INSERT INTO deals (product_id, detected_on, kind, score, price, status) VALUES (6, ?, 'oferta', 1, 1, 'approved')", (d(-1),))
m.execute("INSERT INTO daily_pick (day, deal_id, chosen_at) VALUES (date('now'), ?, 'x')", (old_pick,))
check("marcados a diario", sorted(daily_marked(m)), [2, 3, 6])

if failures:
    print(f"{len(failures)} fallos:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
