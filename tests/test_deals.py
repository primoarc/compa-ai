"""Detector de precios anómalos.  Ejecutar:  python tests/test_deals.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare import deals  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


def row(store, items):
    return {"ok": True, "store": store.title(), "store_key": store, "items": items}


def item(name, price, available=True):
    return {"name": name, "price": price, "available": available, "url": "#", "image": None}


TV = 'Televisor Samsung 65" LED 4K UHD UN65U8000FPX'

# --- caso real: mismo número de parte, una tienda muy por debajo -----------
found = deals.find_anomalies("televisor 65", [
    row("curacao",    [item(TV, 4197.0)]),
    row("kemik",      [item(TV, 5426.0)]),
    row("radioshack", [item(TV, 7999.0)]),
])
check("detecta el outlier entre tiendas", len(found), 1)
if found:
    a = found[0]
    check("marca la tienda barata", a.store_key, "curacao")
    check("confianza alta con 3 tiendas", a.confidence, "alta")
    check("compara contra la mediana de las otras", a.reference, 6712.5)

# --- generaciones distintas NO se mezclan (FPX vs HPX) --------------------
found = deals.find_anomalies("televisor 65", [
    row("curacao",    [item('Televisor Samsung 65" UN65U8000FPX', 4197.0)]),
    row("radioshack", [item('Televisor Samsung 65" UN65U8000HPX', 5299.0)]),
])
check("FPX y HPX son modelos distintos", found, [])

# --- agotado no sirve aunque el precio sea un error ------------------------
found = deals.find_anomalies("televisor 65", [
    row("curacao",    [item(TV, 4197.0, available=False)]),
    row("kemik",      [item(TV, 5426.0)]),
    row("radioshack", [item(TV, 7999.0)]),
])
check("ignora el producto agotado", found, [])

# --- con solo dos tiendas se exige más desvío -----------------------------
found = deals.find_anomalies("televisor 65", [
    row("curacao",    [item(TV, 4200.0)]),   # -30% contra 6000: insuficiente
    row("radioshack", [item(TV, 6000.0)]),
])
check("dos tiendas y -30% no alcanza", found, [])

found = deals.find_anomalies("televisor 65", [
    row("curacao",    [item(TV, 2900.0)]),   # -52% contra 6000: sí
    row("radioshack", [item(TV, 6000.0)]),
])
check("dos tiendas y -52% sí", len(found), 1)
if found:
    check("dos tiendas dan confianza media", found[0].confidence, "media")

# --- precios muy bajos se descartan (suelen ser accesorios) ---------------
found = deals.find_anomalies("televisor 65", [
    row("curacao",    [item("Cable HDMI", 50.0)]),
    row("radioshack", [item("Cable HDMI", 900.0)]),
])
check("bajo el mínimo no se evalúa", found, [])

# --- una tienda sola no es comparación ------------------------------------
found = deals.find_anomalies("televisor 65", [row("curacao", [item(TV, 4197.0)])])
check("una sola tienda no basta", found, [])

# --- el borrador de publicación incluye lo esencial ------------------------
found = deals.find_anomalies("televisor 65", [
    row("curacao",    [item(TV, 4197.0)]),
    row("kemik",      [item(TV, 5426.0)]),
    row("radioshack", [item(TV, 7999.0)]),
])
texto = deals.post_text(found[0])
check("el borrador trae el precio", "Q4,197" in texto, True)
check("el borrador trae el porcentaje", "-37%" in texto, True)

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("deals: todos los casos OK")
