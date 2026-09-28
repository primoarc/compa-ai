"""Parseo del enumerador de Max (Constructor.io) contra respuestas guardadas.

Se ejecuta sin red:  python tests/test_ingest_max.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gt_compare.ingest.max import parse_groups, records_from_result  # noqa: E402
from gt_compare.stores import Store  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "max"
STORE = Store("max", "Max Distelsa", "www.max.com.gt", kind="max")

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


# --- árbol de grupos ---------------------------------------------------------
roots, base = parse_groups(json.loads((FIX / "groups.json").read_text()))
check("grupo raíz", roots, [("1339", 29764)])
check("categorías Base", base, {"1346", "1347", "28786", "16731"})

# --- resultados ---------------------------------------------------------------
page = json.loads((FIX / "browse_page.json").read_text())
recs = {}
for item in page["response"]["results"]:
    for r in records_from_result(STORE, item, base):
        recs[r.store_sku] = r
check("un registro por resultado", sorted(recs), ["MM00DEMO01", "d0a1b2c3", "e1f2a3b4"])

r = recs["d0a1b2c3"]  # oferta: final 130, regular 200, sin EAN
check("depiladora precio", r.price, 130.0)
check("depiladora precio lista", r.list_price, 200.0)
check("depiladora ean", r.ean, None)
check("depiladora marca", r.brand, "Marca Demo")
check("depiladora disponible", r.available, 50)
check("depiladora categoría", r.store_category,
      "Belleza y Cuidado Personal > Afeitado y depilación > Depiladoras")
check("depiladora url", r.url, "https://www.max.com.gt/depiladora-demo-5-en-1-d0a1b2c3")
check("depiladora primer sku", r.first_sku, True)

r = recs["MM00DEMO01"]  # EAN numérico
check("scooter precio", r.price, 3200.0)
check("scooter precio lista", r.list_price, 4000.0)
check("scooter ean", r.ean, "1234567000015")
check("scooter marca", r.brand, "Acme")
check("scooter categoría", r.store_category, "Movilidad > Scooters > Scooters para adultos")

r = recs["e1f2a3b4"]  # sin descuento: regular == final
check("licor precio", r.price, 60.0)
check("licor sin precio lista", r.list_price, None)
check("licor categoría", r.store_category,
      "Bebidas > Licores fuertes > Licores listos para tomar (RTD)")

# --- EAN numérico que perdió el cero inicial (UPC-A) --------------------------
item = {"value": "X", "data": {"id": "z1", "final_price": 10, "EAN": 12345678905}}
check("upc-a con cero restituido", records_from_result(STORE, item, base)[0].ean, "012345678905")

# --- variaciones: un registro por variación, solo la primera es first_sku -----
item = {
    "value": "Camisa",
    "data": {"id": "p1", "final_price": 100, "regular_price": 100, "EAN": "1234567000022"},
    "variations": [
        {"value": "Camisa S", "data": {"variation_id": "p1-s", "final_price": 90, "regular_price": 100,
                                       "EAN": "1234567000039"}},
        {"value": "Camisa M", "data": {"variation_id": "p1-m"}},
    ],
}
vr = records_from_result(STORE, item, base)
check("variaciones skus", [v.store_sku for v in vr], ["p1-s", "p1-m"])
check("variaciones first_sku", [v.first_sku for v in vr], [True, False])
check("variación precio lista", [v.list_price for v in vr], [100.0, None])
check("variación no hereda EAN del padre", [v.ean for v in vr], ["1234567000039", None])

if failures:
    print(f"{len(failures)} fallas:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
