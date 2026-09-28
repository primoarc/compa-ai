"""Prueba contra Turso real que lo que usa el código funciona.

    GT_COMPARE_DB_URL=libsql://... TURSO_AUTH_TOKEN=... python scripts/check_turso.py

Turso no permite tablas virtuales ni triggers TEMP, así que la prueba crea
objetos permanentes con prefijo `zz_check_` (tabla, índice de búsqueda FTS5 y
trigger), corre todo lo que usa el código sobre ellos y los borra al final,
también si algo falla. No toca las tablas de la aplicación. Además lista qué
objetos del esquema tiene la base (solo lectura).
Sale con código 1 si algo falla o si quedara algún objeto `zz_check_`.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare import db as dbmod  # noqa: E402

PREFIX = "zz_check_"

SETUP = [
    ("limpiar restos de una prueba anterior", f"DROP TRIGGER IF EXISTS {PREFIX}t_ai", []),
    ("", f"DROP TABLE IF EXISTS {PREFIX}search", []),
    ("", f"DROP TABLE IF EXISTS {PREFIX}t", []),
    ("tabla", f"CREATE TABLE {PREFIX}t (id INTEGER PRIMARY KEY AUTOINCREMENT, k TEXT UNIQUE, "
     "name TEXT, price REAL, n INTEGER, d TEXT)", []),
    ("FTS5 con contenido externo", f"CREATE VIRTUAL TABLE {PREFIX}search USING fts5(name, content='{PREFIX}t', "
     "content_rowid='id', tokenize='unicode61 remove_diacritics 2')", []),
    ("trigger con cuerpo de varias sentencias",
     f"CREATE TRIGGER {PREFIX}t_ai AFTER INSERT ON {PREFIX}t BEGIN "
     f"INSERT INTO {PREFIX}search (rowid, name) VALUES (new.id, new.name); "
     f"INSERT INTO {PREFIX}search ({PREFIX}search, rowid, name) VALUES ('delete', new.id, new.name); "
     f"INSERT INTO {PREFIX}search (rowid, name) VALUES (new.id, new.name); END", []),
]

CHECKS = [
    ("tipos: texto, float, entero, null", f"INSERT INTO {PREFIX}t (k, name, price, n, d) VALUES (?, ?, ?, ?, ?)",
     ["a", "Audífonos Sony WH-1000XM5", 1899.5, 3, None]),
    ("INSERT OR REPLACE", f"INSERT OR REPLACE INTO {PREFIX}t (id, k, name, price, n) "
     "VALUES (2, 'b', 'Licuadora Oster', 450.0, 1)", []),
    ("upsert con WHERE", f"INSERT INTO {PREFIX}t (k, name, price, n) VALUES ('a', 'x', 1899.5, 9) "
     f"ON CONFLICT(k) DO UPDATE SET n=excluded.n WHERE {PREFIX}t.price IS NOT excluded.price", []),
    ("INSERT OR IGNORE", f"INSERT OR IGNORE INTO {PREFIX}t (k, name) VALUES ('a', 'duplicado')", []),
    ("INSERT de varias filas (copia)", f"INSERT OR IGNORE INTO {PREFIX}t (k, name, price) VALUES (?, ?, ?), (?, ?, ?)",
     ["c", "Televisor Samsung 55", 3999.0, "d", "Cafetera de goteo", 250.0]),
    ("leer tipos", f"SELECT k, price, n, d FROM {PREFIX}t WHERE k='a'", []),
    ("el trigger llenó el índice", f"SELECT COUNT(*) FROM {PREFIX}search", []),
    ("búsqueda con acentos y prefijo, orden por rank",
     f"SELECT {PREFIX}t.k FROM {PREFIX}search JOIN {PREFIX}t ON {PREFIX}t.id = {PREFIX}search.rowid "
     f"WHERE {PREFIX}search MATCH ? ORDER BY {PREFIX}search.rank", ['"audifono"*']),
    ("fechas y + para evitar índice", "SELECT date('now', '-3 day') <= date('now'), +1", []),
    ("PRAGMA table_info", f"PRAGMA table_info({PREFIX}t)", []),
    ("transacción en el mismo pipeline", "BEGIN", []),
    ("escritura dentro de la transacción", f"UPDATE {PREFIX}t SET n = COALESCE(n, 0) + 1 WHERE k = 'b'", []),
    ("commit", "COMMIT", []),
]

TEARDOWN = [
    ("borrar trigger", f"DROP TRIGGER IF EXISTS {PREFIX}t_ai", []),
    ("borrar índice de búsqueda", f"DROP TABLE IF EXISTS {PREFIX}search", []),
    ("borrar tabla", f"DROP TABLE IF EXISTS {PREFIX}t", []),
]


def run(turso: dbmod.TursoDatabase, steps: list) -> list[dict]:
    """Un pipeline: cada sentencia se ejecuta aunque otra falle."""
    requests = [{"type": "execute", "stmt": {"sql": sql, "args": [dbmod._turso_arg(a) for a in args]}}
                for _, sql, args in steps]
    requests.append({"type": "close"})
    resp = turso._client.post("/v2/pipeline", json={"requests": requests}, timeout=60.0)
    if resp.status_code != 200:
        raise RuntimeError(f"Turso respondió HTTP {resp.status_code}")
    return resp.json().get("results", [])


def report(steps: list, results: list[dict]) -> int:
    failed = 0
    for (label, _, _), item in zip(steps, results):
        if not label:
            continue
        if item.get("type") == "ok":
            rows = [[dbmod._turso_value(c) for c in r] for r in item["response"]["result"].get("rows", [])][:3]
            print(f"ok     {label}{'  -> ' + str(rows) if rows else ''}")
        else:
            failed += 1
            print(f"FALLA  {label}: {(item.get('error') or {}).get('message')}")
    return failed


def main() -> int:
    url = os.getenv("GT_COMPARE_DB_URL", "")
    if not url.startswith(("libsql://", "https://")):
        sys.exit("Falta GT_COMPARE_DB_URL (libsql://...) y TURSO_AUTH_TOKEN en el entorno.")
    turso = dbmod.TursoDatabase(url, os.getenv("TURSO_AUTH_TOKEN", ""))
    failed = 0
    try:
        steps = SETUP + CHECKS
        failed += report(steps, run(turso, steps))
    except Exception as exc:  # noqa: BLE001 - se informa sin la URL
        print(f"FALLA  conexión: {type(exc).__name__}")
        failed += 1
    finally:
        try:
            failed += report(TEARDOWN, run(turso, TEARDOWN))
        except Exception as exc:  # noqa: BLE001
            print(f"FALLA  limpieza: {type(exc).__name__}")
            failed += 1

    left = turso.query(f"SELECT name FROM sqlite_master WHERE name LIKE '{PREFIX}%'")
    if left:
        failed += 1
        print(f"FALLA  quedaron objetos de prueba: {[r['name'] for r in left]}")
    else:
        print("ok     no quedó ningún objeto zz_check_")

    expected_db = sqlite3.connect(":memory:")
    expected_db.executescript(dbmod.SCHEMA)
    q = "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    expected = {r[0] for r in expected_db.execute(q)}
    have = {r["name"] for r in turso.query(q)}
    missing = sorted(expected - have)
    print(f"\nesquema: {len(have & expected)} de {len(expected)} objetos"
          + (f"; faltan {missing}" if missing else "; completo"))
    for table in ("products", "price_history"):
        if table in have:
            n = turso.query_one(f"SELECT COUNT(*) AS n FROM {table}")
            print(f"filas en {table}: {n['n'] if n else '?'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
