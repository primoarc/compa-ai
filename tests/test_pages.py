"""Páginas con historial.  Ejecutar:  python tests/test_pages.py"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from gt_compare import db as dbmod, pages  # noqa: E402
from gt_compare.history import Observation, apply_observations, today_utc  # noqa: E402
from gt_compare.ingest.types import ProductRecord  # noqa: E402
from gt_compare.products import upsert_products  # noqa: E402
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


def seed(store, sku, name, hist, price, image=None):
    rec = ProductRecord(store, sku, f"https://{store}.example/{sku}", name, price, image=image)
    pid = upsert_products(mem, [rec], TODAY)[sku]
    for off, p in hist:
        apply_observations(mem, d(off), [Observation(pid, p, None, 1)], None)
    apply_observations(mem, TODAY, [Observation(pid, price, None, 1)], None)
    mem.execute("UPDATE products SET category_id='televisores_video/televisores' WHERE id=?", (pid,))
    return pid


tv = seed("siman", "1", "Televisor Samsung 55 UN55DU7000", [(o, 5000.0) for o in range(-40, 0)], 3000.0)
lap = seed("cemaco", "2", "Laptop Lenovo IdeaPad", [(o, 4000.0) for o in range(-40, 0)], 3990.0)
err = seed("walmart", "3", "Refrigeradora LG 14 pies", [(o, 6000.0) for o in range(-40, 0)], 900.0)
hi = seed("siman", "4", "Licuadora Oster", [(o, 500.0 if o < -10 else 400.0) for o in range(-40, 0)], 480.0)
mem.executemany(
    """INSERT INTO deals (product_id, detected_on, kind, score, price, reference, features, status)
       VALUES (?,?,?,?,?,?,?,?)""",
    [(tv, TODAY, "oferta_fuerte", 40.0, 3000.0, 5000.0, "{}", "published"),
     (err, TODAY, "posible_error", 90.0, 900.0, 6000.0, "{}", "pending")],
)

client = TestClient(app)

# --- ficha ------------------------------------------------------------------
r = client.get(f"/p/{tv}")
check("ficha 200", r.status_code, 200)
check("ficha: badge buen precio", 'class="badge good">Buen precio' in r.text, True)
check("ficha: gráfica", '<svg class="chart"' in r.text, True)
check("ficha: % contra precio normal", "40% menos" in r.text, True)
wa = unquote(r.text.split('href="https://wa.me/?text=')[1].split('"')[0])
check("whatsapp prellenado", wa.startswith("Televisor Samsung 55 UN55DU7000 a Q3,000.00 en Siman, 40% menos"), True)
check("ficha: og dinámico", f'/og/p/{tv}.png' in r.text, True)
check("ficha: JSON-LD de producto", '"@type": "Product"' in r.text, True)
check("sin itálicas ni fuentes externas", ("<em>" in r.text, "fonts.googleapis" in r.text), (False, False))

r = client.get(f"/p/{hi}")
check("badge esperá", "Esperá, ha estado más barato" in r.text, True)
r = client.get(f"/p/{lap}")
check("badge precio normal", 'class="badge normal">Precio normal' in r.text, True)
check("ficha inexistente", client.get("/p/999999").status_code, 404)
mem.execute("UPDATE products SET cur_cash_price=3800 WHERE id=?", (lap,))
check("ficha muestra la condición de pago", "Con tarjeta. En efectivo: <strong>Q3,800.00</strong>" in client.get(f"/p/{lap}").text, True)
mem.execute("UPDATE products SET cur_cash_price=NULL WHERE id=?", (lap,))

# --- ofertas ----------------------------------------------------------------
r = client.get("/ofertas")
check("ofertas 200", r.status_code, 200)
check("ofertas con caché corta", "s-maxage=60" in r.headers["cache-control"], True)
check("oferta publicada aparece", "Televisor Samsung 55" in r.text, True)
check("price error sin aprobar no aparece", "Refrigeradora LG" in r.text, False)
mem.execute("""INSERT INTO deals (product_id, detected_on, kind, score, price, reference, features, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (lap, TODAY, "oferta_fuerte", 30.0, 3990.0, 6000.0, '{"reference_kind": "otras_tiendas"}', "published"))
r = client.get("/ofertas")
check("referencia de otras tiendas se nombra así", "34% menos que otras tiendas" in r.text, True)
r = client.get(f"/p/{lap}")
check("ficha con referencia de otras tiendas", "En otras tiendas <s>Q6,000.00</s>" in r.text, True)
mem.execute("DELETE FROM deals WHERE product_id=?", (lap,))
r = client.get("/ofertas?tienda=cemaco")
check("filtro por tienda", "Televisor Samsung 55" in r.text, False)
r = client.get("/ofertas?categoria=televisores_video")
check("filtro por categoría", "Televisor Samsung 55" in r.text, True)
r = client.get("/ofertas?categoria=audio")
check("filtro por otra categoría", "Televisor Samsung 55" in r.text, False)

# --- OG ---------------------------------------------------------------------
r = client.get(f"/og/p/{tv}.png")
check("og png", (r.status_code, r.headers["content-type"], r.content[:4]), (200, "image/png", b"\x89PNG"))
r = client.get(f"/og/p/{tv}-9x16.png")
from PIL import Image  # noqa: E402
import io  # noqa: E402
check("og vertical 9:16", Image.open(io.BytesIO(r.content)).size, (1080, 1920))
check("og nombre inválido", client.get("/og/p/abc.png").status_code, 404)

# --- alertas ----------------------------------------------------------------
ok = {"product_id": tv, "whatsapp": "5555 1234", "target_price": "2800", "consent": True}
check("alerta sin permiso", client.post("/api/alertas", json={**ok, "consent": False}).status_code, 422)
check("alerta número inválido", client.post("/api/alertas", json={**ok, "whatsapp": "123"}).status_code, 422)
check("alerta ok", client.post("/api/alertas", json=ok).status_code, 200)
client.post("/api/alertas", json={**ok, "target_price": "2700"})
rows = mem.query("SELECT whatsapp, target_price, consent_text FROM alert_subscriptions")
check("una alerta por número y producto", [(r["whatsapp"], r["target_price"]) for r in rows],
      [("+50255551234", 2700.0)])
check("se guarda el texto del consentimiento", rows[0]["consent_text"], pages.ALERT_CONSENT)

# --- envío de alertas apagado por flag ---------------------------------------
from gt_compare import alerts  # noqa: E402

os.environ.pop("ALERTS_SEND_ENABLED", None)
mem.execute("UPDATE products SET cur_price=2600 WHERE id=?", (tv,))
check("alerta que toca", [a["whatsapp"] for a in alerts.due(mem, TODAY)], ["+50255551234"])
check("envío apagado no manda nada", alerts.send_due(mem, TODAY), {"due": 1, "sent": 0, "enabled": False})
os.environ["ALERTS_SEND_ENABLED"] = "1"
try:
    alerts.send_due(mem, TODAY)
    failures.append("flag encendido sin proveedor debería fallar")
except RuntimeError:
    pass
os.environ.pop("ALERTS_SEND_ENABLED", None)
mem.execute("UPDATE products SET cur_price=3000 WHERE id=?", (tv,))

# --- panel ------------------------------------------------------------------
os.environ.pop("ADMIN_TOKEN", None)
check("panel sin token configurado", client.get("/admin").status_code, 404)
os.environ["ADMIN_TOKEN"] = "secreto-de-prueba"
r = client.get("/admin")
check("sin cookie: formulario de entrada", (r.status_code, 'action="/admin/login"' in r.text), (200, True))
check("el token por URL ya no entra", "gtc_admin" in client.get("/admin?token=secreto-de-prueba").headers.get("set-cookie", ""), False)
r = client.post("/admin/login", data={"token": "malo"}, follow_redirects=False)
check("token malo", (r.status_code, r.headers["location"], "gtc_admin" in r.headers.get("set-cookie", "")),
      (303, "/admin?error=1", False))
r = client.post("/admin/login", data={"token": "secreto-de-prueba"}, follow_redirects=False)
cookie = r.headers.get("set-cookie", "")
check("token bueno: cookie httpOnly y SameSite estricto, sin token en la URL",
      (r.status_code, r.headers["location"], "httponly" in cookie.lower(), "samesite=strict" in cookie.lower(),
       "secreto" in cookie),
      (303, "/admin", True, True, False))
r = client.get("/admin")
check("panel muestra la cola", "Refrigeradora LG" in r.text, True)
check("panel sugiere oferta del día", "<strong>Sugerida</strong>" in r.text, True)
deal_id = mem.query_one("SELECT id FROM deals WHERE product_id=?", (err,))["id"]
client.post(f"/admin/aprobar/{deal_id}")
check("aprobado", mem.query_one("SELECT status FROM deals WHERE id=?", (deal_id,))["status"], "approved")
check("aprobado aparece en ofertas", "Refrigeradora LG" in client.get("/ofertas").text, True)

check("oferta del día sin elegir", client.get("/oferta-del-dia").status_code, 200)
client.post(f"/admin/dia/{deal_id}")
r = client.get("/oferta-del-dia", follow_redirects=False)
check("oferta del día redirige a la ficha", (r.status_code, r.headers["location"]), (302, f"/p/{err}"))

# --- "más barato que en X": sección aparte, nunca oferta del día --------------------
mb = seed("max", "5", "Set de cama Simmons queen", [], 3524.0)
mb_dup = seed("max", "6", "Set de cama  Simmons queen", [], 3524.0)   # mismo producto, otro SKU
deal_mb = mem.execute(
    """INSERT INTO deals (product_id, detected_on, kind, score, price, reference, features, status)
       VALUES (?,?,?,?,?,?,?,?)""",
    (mb, TODAY, "mas_barato", 50.0, 3524.0, 6190.0,
     '{"reference_kind": "otras_tiendas", "peer_store": "walmart"}', "published"))
mem.execute("""INSERT INTO deals (product_id, detected_on, kind, score, price, reference, features, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (mb_dup, TODAY, "mas_barato", 49.0, 3524.0, 6190.0,
             '{"reference_kind": "otras_tiendas", "peer_store": "walmart"}', "published"))
r = client.get("/ofertas")
main_list, _, cheaper_list = r.text.partition("Más barato que en otra tienda</h2>")
check("más barato no está entre las ofertas", "Set de cama Simmons" in main_list, False)
check("más barato en su sección, con la tienda", ("Set de cama Simmons" in cheaper_list,
                                                  "Más barato que en Walmart" in cheaper_list), (True, True))
check("mismo producto de la misma tienda una sola vez", cheaper_list.count("Set de cama"), 1)
check("más barato no puede ser oferta del día", client.post(f"/admin/dia/{deal_mb}").status_code, 400)
check("ficha de más barato", "Más barato que en Walmart Guatemala <s>Q6,190.00</s>" in client.get(f"/p/{mb}").text, True)

# --- mismo grupo entre tiendas: una sola vez en /ofertas -------------------------
twin = seed("cemaco", "7", "Televisor Samsung 55 UN55DU7000 (otra tienda)", [(o, 5000.0) for o in range(-40, 0)], 3000.0)
mem.execute("""INSERT INTO deals (product_id, detected_on, kind, score, price, reference, features, status)
               VALUES (?,?,?,?,?,?,?,?)""", (twin, TODAY, "oferta_fuerte", 39.0, 3000.0, 5000.0, "{}", "published"))
cid = mem.execute("INSERT INTO clusters (ean, name) VALUES (NULL, 'tv')")
same_store = seed("siman", "8", "Televisor Samsung 55 UN55DU7000 publicado dos veces", [], 3100.0)
mem.executemany("INSERT INTO product_clusters (product_id, cluster_id, method, confidence, decided_at) VALUES (?,?,?,?,?)",
                [(tv, cid, "jev", 0.99, TODAY), (twin, cid, "jev", 0.99, TODAY), (same_store, cid, "jev", 0.99, TODAY)])
r = client.get("/ofertas")
check("mismo grupo: solo la de mayor puntaje", ("Televisor Samsung 55 UN55DU7000</div>" in r.text,
                                                 "(otra tienda)" in r.text), (True, False))
r = client.get(f"/p/{tv}")
others = r.text.partition("En otras tiendas</h2>")[2]
check("ficha: la misma tienda no sale como otra tienda",
      ("Cemaco" in others, "publicado dos veces" in others), (True, False))

anon = TestClient(app)
check("acción sin cookie", anon.post(f"/admin/rechazar/{deal_id}").status_code, 404)
for _ in range(5):
    anon.post("/admin/login", data={"token": "x"}, follow_redirects=False)
check("demasiados intentos", anon.post("/admin/login", data={"token": "x"}).status_code, 429)
client.post("/admin/logout")
check("salir borra la sesión", 'action="/admin/login"' in client.get("/admin").text, True)

# --- miniaturas desde la CDN de la tienda -------------------------------------
from gt_compare.images import thumb  # noqa: E402

check("VTEX pide 112 px", thumb("https://walmartgt.vteximg.com.br/arquivos/ids/791200/a.jpg?v=1", 112),
      "https://walmartgt.vteximg.com.br/arquivos/ids/791200-112-112/a.jpg?v=1")
check("VTEX ya redimensionada se reemplaza", thumb("https://x.vteximg.com.br/arquivos/ids/5-500-500/a.jpg", 112),
      "https://x.vteximg.com.br/arquivos/ids/5-112-112/a.jpg")
check("Max usa width", thumb("https://backoffice.max.com.gt/media/a.jpg?optimize=high", 112),
      "https://backoffice.max.com.gt/media/a.jpg?width=112")
check("otras CDN sin cambio", thumb("https://sears.com.gt/a.jpg", 112), "https://sears.com.gt/a.jpg")

if failures:
    print(f"{len(failures)} fallos:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
