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

# Sin historial propio: otras tiendas más caras no hacen "oferta" ni price error,
# solo "más barato que en X" contra la más barata de ellas.
v = verdict([iv(0, 0, 400.0)], 400.0, peers=[1000.0, 1050.0])
check("sin historial: más barato, no posible error", (v.kind, v.reference, v.reference_kind),
      ("mas_barato", 1000.0, "otras_tiendas"))
# Los colchones de Max: un día de historial, 43% bajo Walmart.
v = verdict([iv(-1, -1, 3524.0), iv(0, 0, 3524.0)], 3524.0, peers=[6190.0])
check("un día de historial contra otra tienda: más barato", v.kind, "mas_barato")
# 6 días no alcanzan; 7 sí.
six = [iv(-6, -1, 5000.0), iv(0, 0, 3000.0)]
check("6 días de historial: sin oferta", verdict(six, 3000.0, peers=[5000.0]).kind, "mas_barato")
seven = [iv(-7, -1, 5000.0), iv(0, 0, 3000.0)]
check("7 días de historial: oferta fuerte", verdict(seven, 3000.0).kind, "oferta_fuerte")
# Poco historial, sin precio anterior ni otras tiendas: nada.
check("solo hoy y sin otras tiendas: nada", verdict([iv(0, 0, 300.0)], 300.0).kind, None)
# Diferencia chica con otra tienda: nada.
check("menos de 15% bajo otra tienda: nada", verdict([iv(0, 0, 900.0)], 900.0, peers=[1000.0]).kind, None)

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

# Historial corto: un posible error solo se mide contra su precio anterior.
short = [iv(-3, -1, 1000.0), iv(0, 0, 300.0)]
v = verdict(short, 300.0)
check("historial corto con caída de 70% contra el precio anterior: posible error",
      (v.kind, v.reference, v.reference_kind), ("posible_error", 1000.0, "anterior"))
v = verdict([iv(-3, -1, 1000.0), iv(0, 0, 700.0)], 700.0)
check("historial corto con caída de 30%: nada", v.kind, None)

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
    # Sin historial propio, el par validado solo sostiene "más barato que en X".
    check("par consistente sostiene más barato", kinds.get(ok_a), "mas_barato")
    check("par contradictorio (otra marca) no sostiene nada", kinds.get(bad_a), None)
    check("se cuenta la baja de clase", out["peer_rejected_downgrades"], 1)
    peer = mem.query_one("SELECT features FROM deals WHERE product_id=?", (ok_a,))["features"]
    check("nombra la tienda más cara", '"peer_store": "walmart"' in peer, True)

    # Otra tienda del grupo más barata, aunque su par no se valide: no es "más barato".
    cheap = seed("max", "9", "Licuadora Ninja Professional 1000W", 1400.0)
    mem.execute("INSERT INTO product_clusters (product_id, cluster_id, method, confidence, decided_at) "
                "SELECT ?, cluster_id, 'ean', 0.92, ? FROM product_clusters WHERE product_id=?", (cheap, TODAY, ok_a))
    await detector.run(mem, TODAY)
    check("si otra tienda del grupo es más barata, no se publica",
          mem.query_one("SELECT status FROM deals WHERE product_id=?", (ok_a,))["status"], "withdrawn")


asyncio.run(ean_validation())


# --- caso HP: paquete con el EAN de la laptop sola -----------------------------
async def bundle_case():
    mem = dbmod.open_db(":memory:")

    def seed(store, sku, name, price, days):
        rec = ProductRecord(store, sku, f"https://x/{store}/{sku}", name, price, ean="0821844146996")
        pid = upsert_products(mem, [rec], TODAY)[sku]
        for off in range(-days, 1):
            apply_observations(mem, d(off), [Observation(pid, price, None, 1)], None)
        return pid

    laptop = seed("walmart", "1", "Hp 15 R5 8gb 512gb 15fc0353la", 4945.0, 30)
    combo = seed("max", "2", "IMPRESORA HP 210 + LAPTOP HP 15-FC0353LA", 10590.0, 30)
    from gt_compare import clusters  # noqa: E402
    out = await clusters.rebuild(mem)
    check("paquete y laptop sola no quedan en el mismo grupo",
          mem.query("SELECT product_id FROM product_clusters"), [])
    check("el par va a revisión como paquete",
          [(r["product_a"], r["product_b"], r["source"]) for r in mem.query("SELECT * FROM match_reviews")],
          [(min(laptop, combo), max(laptop, combo), "paquete")])
    check("se cuenta", out["bundle_review"], 1)
    ok = await detector._validate_peers([({"name": "Hp 15 R5 8gb 512gb 15fc0353la"},
                                          {"name": "IMPRESORA HP 210 + LAPTOP HP 15-FC0353LA"})], None)
    check("la validación de pares rechaza paquete contra laptop", ok, [False])


asyncio.run(bundle_case())

from gt_compare.matching import is_bundle  # noqa: E402

check("qué es paquete", [is_bundle(n) for n in (
    "IMPRESORA HP 210 + LAPTOP HP 15-FC0353LA", "Kit Bocina Luna 2 y Audífonos", "Paquete 6 baterías CR2032",
    "Combo laptop y mouse", "Honor x6e, 4 +256GB", "Juego de Mesa Simon para 8+ Años",
    "Licuadora Black + Decker Ice Crush", "LEGO Speed Champions Ferrari F40 - Kit de Construcción",
    "8GB RAM + SSD 512", "Acondicionador Active+ Keratin")],
    [True, True, True, True, False, False, False, False, False, False])


# --- una oferta que ya no se sostiene en la misma fecha se retira ----------------
async def withdrawn():
    mem = dbmod.open_db(":memory:")
    rec = ProductRecord("siman", "1", "https://x/1", "Televisor Samsung 65", 500.0)
    pid = upsert_products(mem, [rec], TODAY)[rec.store_sku]
    for off in range(-40, 0):
        apply_observations(mem, d(off), [Observation(pid, 2000.0, None, 1)], None)
    apply_observations(mem, TODAY, [Observation(pid, 500.0, None, 1)], None)
    await detector.run(mem, TODAY)
    mem.execute("UPDATE deals SET status='approved' WHERE product_id=?", (pid,))
    # La tienda corrige el precio y el detector vuelve a correr el mismo día.
    apply_observations(mem, TODAY, [Observation(pid, 2000.0, None, 1)], None)
    mem.execute("UPDATE products SET cur_price=2000 WHERE id=?", (pid,))
    out = await detector.run(mem, TODAY)
    check("aprobada que ya no se sostiene: retirada",
          (mem.query_one("SELECT status FROM deals WHERE product_id=?", (pid,))["status"], out["retiradas"]),
          ("withdrawn", 1))


asyncio.run(withdrawn())


async def dry():
    mem = dbmod.open_db(":memory:")
    rec = ProductRecord("siman", "1", "https://x/1", "Licuadora Oster", 700.0)
    pid = upsert_products(mem, [rec], TODAY)[rec.store_sku]
    for off in range(-40, 0):
        apply_observations(mem, d(off), [Observation(pid, 1000.0, None, 1)], None)
    apply_observations(mem, TODAY, [Observation(pid, 700.0, None, 1)], None)
    out = await detector.run(mem, TODAY, dry_run=True)
    check("simulación: devuelve la oferta sin escribirla",
          ([r["kind"] for r in out["filas"]], mem.query("SELECT * FROM deals")), (["oferta_fuerte"], []))


asyncio.run(dry())

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
