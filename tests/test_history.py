"""Historial, normalización de EAN y adaptador Turso.  Ejecutar:  python tests/test_history.py"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from gt_compare import db as dbmod  # noqa: E402
from gt_compare.history import (  # noqa: E402
    Observation, apply_observations, plan_interval_writes, previous_price, series, window_stats,
)
from gt_compare.ingest.types import ProductRecord  # noqa: E402
from gt_compare.products import normalize_ean, upsert_products  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


def iv(start, end, price, list_price=None, available=1):
    return {"start_day": start, "end_day": end, "price": price,
            "list_price": list_price, "available": available}


# --- plan_interval_writes -----------------------------------------------------
last = {1: iv("2026-09-01", "2026-09-05", 100.0)}
ext, trim, ins, ch = plan_interval_writes(last, "2026-09-06", [Observation(1, 100.0, None, 1)])
check("mismo precio al día siguiente extiende", (ext, trim, ins, ch),
      ([("2026-09-06", 1, "2026-09-01")], [], [], set()))

ext, trim, ins, ch = plan_interval_writes(last, "2026-09-05", [Observation(1, 100.0, None, 1)])
check("mismo precio el mismo día no escribe", (ext, trim, ins, ch), ([], [], [], set()))

ext, trim, ins, ch = plan_interval_writes(last, "2026-09-12", [Observation(1, 100.0, None, 1)])
check("hueco > GAP_DAYS abre intervalo nuevo", ins, [(1, "2026-09-12", "2026-09-12", 100.0, None, 1, None)])
check("hueco no extiende", ext, [])

ext, trim, ins, ch = plan_interval_writes(last, "2026-09-06", [Observation(1, 80.0, None, 1)])
check("cambio de precio inserta", ins, [(1, "2026-09-06", "2026-09-06", 80.0, None, 1, None)])
check("cambio de precio marca cambiado", ch, {1})
check("cambio al día siguiente no recorta", trim, [])

ext, trim, ins, ch = plan_interval_writes(last, "2026-09-05", [Observation(1, 80.0, None, 1)])
check("cambio dentro del intervalo recorta a ayer", trim, [("2026-09-04", 1, "2026-09-01")])

one_day = {1: iv("2026-09-05", "2026-09-05", 100.0)}
ext, trim, ins, ch = plan_interval_writes(one_day, "2026-09-05", [Observation(1, 90.0, None, 1)])
check("corrección el mismo día borra y reinserta", trim, [(None, 1, "2026-09-05")])

ext, trim, ins, ch = plan_interval_writes(last, "2026-08-20", [Observation(1, 50.0, None, 1)])
check("observación vieja se ignora", (ext, trim, ins, ch), ([], [], [], set()))

ext, trim, ins, ch = plan_interval_writes(last, "2026-09-06", [Observation(1, 100.0, None, 0)])
check("agotarse cuenta como cambio", ch, {1})

ext, trim, ins, ch = plan_interval_writes(last, "2026-09-06", [Observation(1, 100.0, 150.0, 1)])
check("cambio de precio de lista cuenta como cambio", ch, {1})

ext, trim, ins, ch = plan_interval_writes({}, "2026-09-06", [Observation(7, 10.0, None, 1)])
check("producto nuevo inserta", ins, [(7, "2026-09-06", "2026-09-06", 10.0, None, 1, None)])

ext, trim, ins, ch = plan_interval_writes(last, "2026-09-06", [Observation(1, 100.0, None, 1, 95.0)])
check("cambio del precio de contado cuenta como cambio", ch, {1})

# --- window_stats -----------------------------------------------------------
hist = [iv("2026-08-01", "2026-09-20", 1000.0), iv("2026-09-21", "2026-09-28", 800.0)]
st = window_stats(hist, "2026-09-28", 30)
check("mediana ponderada por días", st.median, 1000.0)
check("mínimo", st.minimum, 800.0)
check("día del mínimo", st.min_day, "2026-09-21")
check("días cubiertos", st.days_covered, 30)

st = window_stats(hist, "2026-09-28", 30, exclude_today=True)
check("sin hoy la ventana termina ayer", st.days_covered, 30)

hist_agotado = [iv("2026-09-01", "2026-09-28", 10.0, available=0)]
check("días agotados no cuentan", window_stats(hist_agotado, "2026-09-28").median, None)

check("sin datos", window_stats([], "2026-09-28").days_covered, 0)

prev = [iv("2026-09-01", "2026-09-20", 1000.0), iv("2026-09-21", "2026-09-28", 800.0)]
check("precio anterior", previous_price(prev, "2026-09-28"), 1000.0)
check("sin intervalo previo", previous_price(prev[:1], "2026-09-10"), None)

# --- normalize_ean ----------------------------------------------------------
check("EAN-13 válido", normalize_ean("7501031311309"), "7501031311309")
check("UPC-A se rellena a 13", normalize_ean("036000291452"), "0036000291452")
check("GTIN-14 con 0 inicial", normalize_ean("07501031311309"), "7501031311309")
check("dígito verificador malo", normalize_ean("7501031311308"), None)
check("prefijo 2 (interno) se descarta", normalize_ean("2000000000008"), None)
check("todo ceros", normalize_ean("0000000000000"), None)
check("largo raro", normalize_ean("12345"), None)
check("con guiones", normalize_ean("750-1031-31130-9"), "7501031311309")
check("vacío", normalize_ean(None), None)

# --- apply_observations + upsert contra SQLite en memoria --------------------
mem = dbmod.open_db(":memory:")


def rec(sku, price, url="https://x/p", first=True, ean=None):
    return ProductRecord(store_key="siman", store_sku=sku, url=url, name="Tele 55 UN55TU7000",
                         price=price, ean=ean, first_sku=first)


ids = upsert_products(mem, [rec("url:https://x/p", 100.0)], "2026-09-01")
legacy_id = ids["url:https://x/p"]
apply_observations(mem, "2026-09-01", [Observation(legacy_id, 100.0, None, 1)], None)
ids = upsert_products(mem, [rec("123", 90.0, ean="7501031311309"), rec("124", 95.0, first=False)],
                      "2026-09-02")
check("el primer SKU adopta la fila heredada", ids["123"], legacy_id)
check("el segundo SKU es producto nuevo", ids["124"] != legacy_id, True)
row = mem.query_one("SELECT store_sku, sku_level, ean, model_code FROM products WHERE id=?",
                    (legacy_id,))
check("fila adoptada", (row["store_sku"], row["sku_level"], row["ean"]),
      ("123", 1, "7501031311309"))
check("código de modelo extraído", row["model_code"], "UN55TU7000")

n = apply_observations(mem, "2026-09-02", [Observation(legacy_id, 90.0, None, 1)], None)
check("cambio contado", n, 1)
apply_observations(mem, "2026-09-03", [Observation(legacy_id, 90.0, None, 1)], None)
rows = series(mem, legacy_id)
check("serie", [(r["start_day"], r["end_day"], r["price"]) for r in rows],
      [("2026-09-01", "2026-09-01", 100.0), ("2026-09-02", "2026-09-03", 90.0)])

upsert_products(mem, [rec("123", 90.0, ean=None)], "2026-09-04")
check("EAN no se pierde si la corrida no lo trae",
      mem.query_one("SELECT ean FROM products WHERE id=?", (legacy_id,))["ean"], "7501031311309")

# Una segunda corrida sin cambios no reescribe productos (cada fila cuenta en Turso)
writes = []
orig_executemany = mem.executemany


def spy(sql, rows):
    rows = list(rows)
    if "INSERT INTO products" in sql:
        writes.append(len(rows))
    return orig_executemany(sql, rows)


mem.executemany = spy  # type: ignore[assignment]
upsert_products(mem, [rec("123", 90.0, ean="7501031311309")], "2026-09-05")
check("sin cambios no se escribe", writes, [])
upsert_products(mem, [rec("123", 85.0, ean="7501031311309")], "2026-09-06")
check("con cambio se escribe", writes, [1])
del mem.executemany

# --- esquema: triggers enteros y migración de bases viejas ---------------------
stmts = dbmod.split_sql(dbmod.SCHEMA)
triggers = [x for x in stmts if x.upper().startswith("CREATE TRIGGER")]
check("triggers completos", (len(triggers), all(x.upper().endswith("END") for x in triggers)), (3, True))

import sqlite3, tempfile, os  # noqa: E402,E401
old = os.path.join(tempfile.mkdtemp(), "old.db")
con = sqlite3.connect(old)
con.executescript("""CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, store_key TEXT NOT NULL,
  store_sku TEXT NOT NULL, url TEXT NOT NULL, name TEXT NOT NULL, brand TEXT, ean TEXT, model_code TEXT,
  image TEXT, store_category TEXT, category_id TEXT, category_source TEXT, category_conf REAL,
  cur_price REAL, cur_list_price REAL, cur_available INTEGER, promo_text TEXT, first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL, sku_level INTEGER NOT NULL DEFAULT 1, UNIQUE (store_key, store_sku));
CREATE TABLE price_history (product_id INTEGER NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL,
  price REAL, list_price REAL, available INTEGER, run_id INTEGER, PRIMARY KEY (product_id, start_day));
INSERT INTO products (store_key, store_sku, url, name, first_seen, last_seen)
  VALUES ('siman', '1', 'u', 'Licuadora Demo', 'd', 'd');""")
con.commit()
con.close()
upgraded = dbmod.open_db(old)
check("columna nueva agregada", "cash_price" in {r["name"] for r in upgraded.query("PRAGMA table_info(price_history)")}, True)
check("índice de búsqueda reconstruido",
      upgraded.query("SELECT rowid FROM product_search WHERE product_search MATCH 'licuadora'"), [{"rowid": 1}])

# --- Turso: formato de la pipeline ------------------------------------------
sent = []


def handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    sent.append((request.url.path, request.headers["authorization"], body))
    results = []
    for r in body["requests"]:
        if r["type"] == "close":
            results.append({"type": "ok", "response": {"type": "close"}})
            continue
        results.append({"type": "ok", "response": {"type": "execute", "result": {
            "cols": [{"name": "id"}, {"name": "price"}, {"name": "name"}, {"name": "x"}],
            "rows": [[{"type": "integer", "value": "5"}, {"type": "float", "value": 9.5},
                      {"type": "text", "value": "tv"}, {"type": "null"}]],
            "affected_row_count": 1, "last_insert_rowid": "42",
        }}})
    return httpx.Response(200, json={"results": results})


turso = dbmod.TursoDatabase("libsql://demo.turso.io", "tok", transport=httpx.MockTransport(handler))
check("Turso query decodifica tipos", turso.query("SELECT ?, ?, ?, ?", (5, 9.5, "tv", None)),
      [{"id": 5, "price": 9.5, "name": "tv", "x": None}])
path, auth, body = sent[-1]
check("Turso ruta", path, "/v2/pipeline")
check("Turso token", auth, "Bearer tok")
check("Turso args", body["requests"][0]["stmt"]["args"],
      [{"type": "integer", "value": "5"}, {"type": "float", "value": 9.5},
       {"type": "text", "value": "tv"}, {"type": "null"}])
check("Turso cierra la conexión", body["requests"][-1], {"type": "close"})
check("Turso execute devuelve rowid", turso.execute("INSERT INTO t VALUES (1)"), 42)
turso.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(450)])
check("Turso executemany por lotes de 200", [len(b["requests"]) - 1 for _, _, b in sent[-3:]],
      [200, 200, 50])


def failing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"results": [
        {"type": "error", "error": {"message": "no such table"}}, {"type": "ok"}]})


try:
    dbmod.TursoDatabase("https://d", "t", transport=httpx.MockTransport(failing)).query("SELECT 1")
    failures.append("Turso error: se esperaba excepción")
except RuntimeError:
    pass

# Los errores no muestran el host de la base (los logs de Actions son públicos)
for handler_ in (lambda r: httpx.Response(401), lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r))):
    try:
        dbmod.TursoDatabase("libsql://secreta-org.turso.io", "tok-secreto",
                            transport=httpx.MockTransport(handler_)).query("SELECT 1")
    except RuntimeError as exc:
        text = f"{exc!r} {exc.__cause__!r} {exc.__context__!r}"
        check("error sin host ni token", ("secreta-org" in text, "tok-secreto" in text), (False, False))

# --- contador de escrituras y aviso del 80% del plan de Turso ------------------
from gt_compare.ingest import __main__ as cli  # noqa: E402

usage = dbmod.open_db(":memory:")
usage.rows_written = 0
usage.executemany("INSERT INTO runs (store_key, kind, started_at, status) VALUES (?,?,?,?)",
                  [("a", "ingest", "x", "ok"), ("b", "ingest", "x", "ok")])
check("SQLite cuenta filas escritas", usage.rows_written, 2)
line = cli.record_writes(usage, "2026-09-28")
check("línea del mes", line.startswith("Escrituras del mes 2026-09: 2 filas"), True)
usage.rows_written = int(cli.WRITE_LIMIT * 0.8)
check("aviso desde el 80%", cli.record_writes(usage, "2026-09-29").startswith("AVISO: "), True)
check("el mes suma corridas",
      usage.query_one("SELECT rows_written FROM db_usage WHERE month='2026-09'")["rows_written"],
      2 + int(cli.WRITE_LIMIT * 0.8))
check("Turso cuenta affected_row_count", turso.rows_written > 0, True)

if failures:
    print(f"{len(failures)} fallos:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
