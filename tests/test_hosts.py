"""Reparto de tiendas entre Actions y la Mac.  Ejecutar:  python tests/test_hosts.py"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare import db as dbmod  # noqa: E402
from gt_compare.ingest import runner  # noqa: E402
from gt_compare.ingest.hosts import LOCAL_STORES, all_stores, stores_for  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


actions, local = set(stores_for("actions")), set(stores_for("local"))
everything = set(all_stores())
check("son 13 tiendas", len(everything), 13)
check("ninguna tienda en los dos lados", actions & local, set())
check("entre los dos cubren las 13", actions | local, everything)
check("las que GitHub no alcanza van en la Mac", local, {"kemik", "curacao", "radioshack"})
check("LOCAL_STORES solo nombra tiendas que existen", LOCAL_STORES - everything, set())
try:
    stores_for("nube")
    failures.append("where inválido debería fallar")
except ValueError:
    pass

# --- corridas colgadas en 'running' y cadencia con --where ---------------------
mem = dbmod.open_db(":memory:")
mem.executemany("INSERT INTO runs (store_key, kind, started_at, status) VALUES (?,?,?,?)", [
    ("epa", "ingest", "2026-09-01T23:05:00+00:00", "running"),     # corrida cancelada, vieja
    ("siman", "ingest", "2099-01-01T00:00:00+00:00", "running"),   # en curso: no se toca
])
check("se cierra la corrida vieja", runner.close_abandoned(mem), 1)
check("estados", {r["store_key"]: r["status"] for r in mem.query("SELECT store_key, status FROM runs")},
      {"epa": "abandoned", "siman": "running"})

ran = []


async def fake_run_store(db, store, *, day=None, limit=0, enumerator=None):
    ran.append(store.key)
    return runner.RunResult(store.key, 0, "ok", 0, 0, 0, 0, 0.0, "")


runner.run_store = fake_run_store  # type: ignore[assignment]
mem.execute("INSERT INTO runs (store_key, kind, started_at, status) VALUES ('novex','ingest','2026-09-28T09:00:00+00:00','ok')")
asyncio.run(runner.run_all(mem, only=["novex", "sears"], day="2026-09-29", cadence=True))
check("con --where se respeta la cadencia (Novex cada 3 días)", ran, ["sears"])
ran.clear()
asyncio.run(runner.run_all(mem, only=["novex"], day="2026-09-29"))
check("con --store a mano no", ran, ["novex"])

if failures:
    print(f"{len(failures)} fallos:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
