"""Prueba contra Turso real que lo que usa el código funciona, sin modificar la base.

    GT_COMPARE_DB_URL=libsql://... TURSO_AUTH_TOKEN=... python scripts/check_turso.py

Todo corre en una sola conexión (un pipeline) y sobre objetos TEMP, que
desaparecen al cerrarla: no toca las tablas de la base. Además lista qué
objetos del esquema tiene la base y cuáles faltan (solo lectura).
Sale con código 1 si algo falla.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare import db as dbmod  # noqa: E402

# Lo mismo que usa el código, sobre tablas temporales. Cada entrada: (qué se prueba, SQL, args).
CHECKS: list[tuple[str, str, list]] = [
    ("tabla temporal", "CREATE TEMP TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, k TEXT UNIQUE, "
     "name TEXT, price REAL, n INTEGER, d TEXT)", []),
    ("FTS5 con contenido externo", "CREATE VIRTUAL TABLE temp.t_search USING fts5(name, content='t', "
     "content_rowid='id', tokenize='unicode61 remove_diacritics 2')", []),
    ("trigger con cuerpo de varias sentencias",
     "CREATE TEMP TRIGGER t_ai AFTER INSERT ON t BEGIN "
     "INSERT INTO t_search (rowid, name) VALUES (new.id, new.name); "
     "INSERT INTO t_search (t_search, rowid, name) VALUES ('delete', new.id, new.name); "
     "INSERT INTO t_search (rowid, name) VALUES (new.id, new.name); END", []),
    ("tipos: texto, float, entero, null", "INSERT INTO t (k, name, price, n, d) VALUES (?, ?, ?, ?, ?)",
     ["a", "Audífonos Sony WH-1000XM5", 1899.5, 3, None]),
    ("INSERT OR REPLACE", "INSERT OR REPLACE INTO t (id, k, name, price, n) VALUES (2, 'b', 'Licuadora Oster', 450.0, 1)", []),
    ("upsert con WHERE", "INSERT INTO t (k, name, price, n) VALUES ('a', 'x', 1899.5, 9) "
     "ON CONFLICT(k) DO UPDATE SET n=excluded.n WHERE t.price IS NOT excluded.price", []),
    ("INSERT OR IGNORE", "INSERT OR IGNORE INTO t (k, name) VALUES ('a', 'duplicado')", []),
    ("leer tipos", "SELECT k, price, n, d FROM t WHERE k='a'", []),
    ("búsqueda con acentos y prefijo, orden por rank",
     "SELECT t.id FROM t_search JOIN t ON t.id = t_search.rowid WHERE t_search MATCH ? ORDER BY t_search.rank",
     ['"audifono"*']),
    ("fechas y + para evitar índice", "SELECT date('now', '-3 day') <= date('now'), +1", []),
    ("PRAGMA table_info", "PRAGMA temp.table_info(t)", []),
    ("transacción en el mismo pipeline", "BEGIN", []),
    ("escritura dentro de la transacción", "UPDATE t SET n = n + 1 WHERE k = 'b'", []),
    ("commit", "COMMIT", []),
]


def main() -> int:
    url = os.getenv("GT_COMPARE_DB_URL", "")
    if not url.startswith(("libsql://", "https://")):
        sys.exit("Falta GT_COMPARE_DB_URL (libsql://...) y TURSO_AUTH_TOKEN en el entorno.")
    turso = dbmod.TursoDatabase(url, os.getenv("TURSO_AUTH_TOKEN", ""))
    stmts = [(sql, args) for _, sql, args in CHECKS]
    requests = [{"type": "execute", "stmt": {"sql": sql, "args": [dbmod._turso_arg(a) for a in args]}}
                for sql, args in stmts]
    requests.append({"type": "close"})
    try:
        resp = turso._client.post("/v2/pipeline", json={"requests": requests})
    except Exception as exc:  # noqa: BLE001 - se informa sin la URL
        sys.exit(f"No se pudo conectar: {type(exc).__name__}")
    if resp.status_code != 200:
        sys.exit(f"Turso respondió HTTP {resp.status_code}")
    failed = 0
    results = resp.json().get("results", [])
    for (label, _, _), item in zip(CHECKS, results):
        if item.get("type") == "ok":
            result = item["response"]["result"]
            rows = [[dbmod._turso_value(c) for c in r] for r in result.get("rows", [])][:3]
            print(f"ok     {label}{'  -> ' + str(rows) if rows else ''}")
        else:
            failed += 1
            print(f"FALLA  {label}: {(item.get('error') or {}).get('message')}")

    # Qué tiene la base (solo lectura) contra el esquema esperado.
    expected_db = sqlite3.connect(":memory:")
    expected_db.executescript(dbmod.SCHEMA)
    q = "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    expected = {r[0] for r in expected_db.execute(q)}
    have = {r["name"] for r in turso.query(q)}
    missing = sorted(expected - have)
    print(f"\nesquema: {len(have & expected)} de {len(expected)} objetos"
          + (f"; faltan {missing} (los crea el próximo copy o run)" if missing else "; completo"))
    for table in ("products", "price_history"):
        if table in have:
            n = turso.query_one(f"SELECT COUNT(*) AS n FROM {table}")
            print(f"filas en {table}: {n['n'] if n else '?'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
