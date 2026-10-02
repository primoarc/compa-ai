"""python -m gt_compare.ingest {run,post,backfill,status,copy}"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

from .. import alerts, categorize, clusters, detector
from ..db import Database, open_db
from ..decide import Decider
from ..decide.core import use_enabled
from ..history import today_utc
from .backfill import backfill
from .hosts import WHERE, stores_for
from .runner import run_all


async def post_process(db: Database, day: str) -> dict:
    """Después de ingerir: categorías, grupos entre tiendas y detector.

    Jev solo entra en los usos encendidos en `JEV_USES`; sin ninguno no se
    crea cliente ni se busca la key.
    """
    uses = {u: use_enabled(u) for u in ("category", "match", "price_cause")}
    decider = Decider(db=db) if any(uses.values()) else None
    seconds: dict[str, float] = {}

    async def timed(name: str, coro):
        started = time.monotonic()
        try:
            return await coro
        finally:
            seconds[name] = time.monotonic() - started
            print(f"post {name:12} {seconds[name]:7.0f}s", flush=True)

    async def sync(fn, *a):
        return fn(*a)

    try:
        out = {
            "categorias": await timed("categorías", categorize.run(db, decider, use_jev=uses["category"])),
            "grupos": await timed("grupos", clusters.rebuild(db, decider if uses["match"] else None)),
            "detector": await timed("detector", detector.run(db, day, decider if uses["price_cause"] else None,
                                                             decider if uses["match"] else None)),
            "alertas": await timed("alertas", sync(alerts.send_due, db, day)),
        }
        out["segundos"] = {k: round(v) for k, v in seconds.items()}
        if decider is not None:
            out["jev"] = {**decider.stats, "usd": round(decider.cost_usd(), 4)}
        return out
    finally:
        if decider is not None:
            await decider.aclose()


# Filas escritas por mes en el plan gratis de Turso (turso.tech/pricing, 28-sep-2026).
WRITE_LIMIT = int(os.getenv("TURSO_WRITE_LIMIT", "10000000"))
WRITE_WARN = 0.8


def record_writes(db: Database, day: str) -> str:
    """Suma lo escrito por este proceso al mes y devuelve la línea para el resumen."""
    month = day[:7]
    db.execute(
        """INSERT INTO db_usage (month, rows_written) VALUES (?, ?)
           ON CONFLICT(month) DO UPDATE SET rows_written = rows_written + excluded.rows_written""",
        (month, db.rows_written),
    )
    row = db.query_one("SELECT rows_written FROM db_usage WHERE month=?", (month,))
    total = int(row["rows_written"]) if row else 0
    share = total / WRITE_LIMIT
    line = f"Escrituras del mes {month}: {total:,} filas ({share:.0%} del plan gratis de Turso)"
    if share >= WRITE_WARN:
        line = f"AVISO: {line}. Ver docs/ingesta-programada.md, sección escrituras."
    return line


# Orden de copia: primero lo que otras tablas referencian.
COPY_TABLES = ("runs", "products", "price_history", "clusters", "product_clusters", "match_reviews",
               "decision_cache", "deals", "daily_pick", "alert_subscriptions")
COPY_BATCH = 5000


COPY_ROWS_PER_INSERT = 100  # filas por sentencia; 100 x 21 columnas queda lejos del límite de parámetros


def copy_db(src: Database, dst: Database) -> dict:
    """Copia todas las filas de `src` a `dst` conservando ids. Es idempotente:
    lo que ya está en el destino se salta (INSERT OR IGNORE). Manda muchas filas
    por sentencia: con una por sentencia, contra Turso eran ~40 s por 1.000."""
    counts = {}
    for table in COPY_TABLES:
        cols = [r["name"] for r in src.query(f"PRAGMA table_info({table})")]
        dst_cols = {r["name"] for r in dst.query(f"PRAGMA table_info({table})")}
        cols = [c for c in cols if c in dst_cols]
        row_marks = "(" + ", ".join("?" * len(cols)) + ")"
        # Reanudar: las filas se copian en orden, así que lo que ya tiene el destino
        # es un prefijo. Se retrocede un lote por si el último quedó a medias.
        have = dst.query_one(f"SELECT COUNT(*) AS n FROM {table}")
        offset = max(0, int(have["n"] if have else 0) - COPY_BATCH)
        offset -= offset % COPY_BATCH
        total = offset
        while True:
            rows = src.query(f"SELECT {', '.join(cols)} FROM {table} ORDER BY rowid LIMIT ? OFFSET ?",
                             (COPY_BATCH, offset))
            if not rows:
                break
            stmts = []
            for i in range(0, len(rows), COPY_ROWS_PER_INSERT):
                chunk = rows[i : i + COPY_ROWS_PER_INSERT]
                sql = (f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES "
                       + ", ".join([row_marks] * len(chunk)))
                stmts.append((sql, [r[c] for r in chunk for c in cols]))
            dst.execute_batch(stmts)
            total += len(rows)
            offset += COPY_BATCH
            logging.getLogger("gt_compare.ingest").info("copia %s: %s filas", table, total)
        counts[table] = total
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gt_compare.ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="recorre los catálogos y guarda el historial")
    run.add_argument("--store", action="append", help="solo estas tiendas (repetible)")
    run.add_argument("--limit", type=int, default=0, help="máximo de SKUs por tienda (0 = todos)")
    run.add_argument("--db", help="ruta o URL de la base (por defecto GT_COMPARE_DB_URL o ~/.gt-compare/history.db)")
    run.add_argument("--no-post", action="store_true", help="solo ingesta, sin categorías/grupos/detector")
    run.add_argument("--where", choices=WHERE,
                     help="solo las tiendas de Actions o de la Mac (gt_compare/ingest/hosts.py)")
    post = sub.add_parser("post", help="categorías, grupos y detector sobre lo ya ingerido")
    post.add_argument("--db")
    post.add_argument("--day", help="día a evaluar (AAAA-MM-DD, por defecto hoy UTC)")
    fill = sub.add_parser("backfill", help="carga los snapshots del barrido viejo")
    fill.add_argument("--store", action="append")
    fill.add_argument("--db")
    cp = sub.add_parser("copy", help="copia una base a otra (p. ej. la local a Turso)")
    cp.add_argument("--from", dest="src", required=True, help="ruta o URL de origen")
    cp.add_argument("--to", dest="dst", help="ruta o URL de destino (por defecto GT_COMPARE_DB_URL)")
    status = sub.add_parser("status", help="últimas corridas por tienda")
    status.add_argument("--db")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db = open_db(args.dst if args.cmd == "copy" else args.db)
    code = 0
    if args.cmd == "backfill":
        print(backfill(db, only=args.store))
    elif args.cmd == "run":
        only = args.store or (stores_for(args.where) if args.where else None)
        # --store a mano ignora la cadencia; --where la respeta (Novex cada 3 días).
        results = asyncio.run(run_all(db, only=only, limit=args.limit, cadence=not args.store))
        for r in results:
            print(f"{r.store_key:12} {r.status:8} {r.records:7} SKUs {r.changed:6} cambios "
                  f"{r.pages:5} págs {r.errors:3} errores {r.seconds:6.0f}s  {r.note}")
        if not args.no_post and not args.limit:
            print(asyncio.run(post_process(db, today_utc())))
        if any(r.status == "failed" for r in results):
            code = 1
    elif args.cmd == "copy":
        print(copy_db(open_db(args.src), db))
    elif args.cmd == "post":
        print(asyncio.run(post_process(db, args.day or today_utc())))
    else:
        for row in db.query(
            """SELECT r.* FROM runs r JOIN (SELECT store_key, MAX(id) id FROM runs
               WHERE kind='ingest' GROUP BY store_key) m ON m.id = r.id ORDER BY r.store_key"""
        ):
            print(f"{row['store_key']:12} {row['status']:8} {row['started_at']} "
                  f"{row['seen'] or 0:7} SKUs  {row['notes'] or ''}")
    if args.cmd != "status":
        print(record_writes(db, today_utc()))
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
