"""Las primeras N de /ofertas en un CSV para revisarlas a mano antes de compartir.

    python scripts/export_ofertas.py [--n 30] [--simular] [--out ~/.gt-compare/revision-ofertas.csv]

Lee la base de GT_COMPARE_DB_URL (Turso) o la local. Sin `--simular` exporta lo
que /ofertas muestra hoy. Con `--simular` corre el detector con el código actual
sin escribir nada (las decisiones de Jev salen del caché; un par sin decisión
guardada va por regla, sin llamar a Jev) y exporta lo que /ofertas mostraría.
Escribe además los "más barato que en X" en un segundo CSV.

El CSV queda fuera del repo: son datos de las tiendas y el repo es público.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare import detector, pages  # noqa: E402
from gt_compare.db import Database, open_db  # noqa: E402
from gt_compare.decide import Decider, schemas  # noqa: E402
from gt_compare.decide.core import JEV_MODEL, JevClient  # noqa: E402
from gt_compare.history import fresh_since, series, today_utc, window_stats  # noqa: E402

METHOD = {"ean": "EAN", "jev": "Jev", "model": "código de modelo", "manual": "manual (panel)"}


def links(db: Database, pid: int, today: str) -> list[dict]:
    """Otras tiendas que la ficha muestra (precio de los últimos 2 días), con cómo se unió cada una."""
    return db.query(
        """SELECT p.store_key, p.name, p.cur_price, pc2.method, pc2.confidence, pc1.method AS own_method
           FROM product_clusters pc1
           JOIN products own ON own.id = pc1.product_id
           JOIN product_clusters pc2 ON pc2.cluster_id = pc1.cluster_id AND pc2.product_id != pc1.product_id
           JOIN products p ON p.id = pc2.product_id AND p.store_key != own.store_key
           WHERE pc1.product_id = ? AND pc1.confidence >= ? AND pc2.confidence >= ?
             AND EXISTS (SELECT 1 FROM price_history h WHERE h.product_id = p.id AND +h.end_day >= ?)
           ORDER BY p.cur_price""",
        (pid, schemas.MATCH_ACCEPT, schemas.MATCH_ACCEPT, fresh_since(today)),
    )


def simulated(db: Database, day: str, n: int) -> tuple[list[dict], list[dict]]:
    """(ofertas, más baratos) como los mostraría /ofertas con el detector actual."""
    # Cliente de relleno: con enabled=False nunca se llama, y así no se busca la key.
    stub = cast(JevClient, SimpleNamespace(model=JEV_MODEL, input_tokens=0))
    cache_only = Decider(db=db, client=stub, enabled=False)
    out = asyncio.run(detector.run(db, day, None, cache_only, dry_run=True))
    rows = sorted((r for r in out["filas"] if r["status"] in pages.FEED_STATUSES
                   and (r["kind"] != "posible_error" or r["status"] == "approved")),
                  key=lambda r: -r["score"])
    info = {}
    ids = [r["product_id"] for r in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for p in db.query(
            f"""SELECT p.id, p.name, p.store_key, p.url, p.cur_price, pc.cluster_id FROM products p
                LEFT JOIN product_clusters pc ON pc.product_id = p.id AND pc.confidence >= ?
                WHERE p.id IN ({",".join("?" * len(chunk))})""", [schemas.MATCH_ACCEPT, *chunk]):
            info[p["id"]] = p
    shown: set = set()

    def pick(kinds: tuple, limit: int) -> list[dict]:
        got = []
        for r in rows:
            if r["kind"] not in kinds:
                continue
            d = {**r, **{k: v for k, v in info[r["product_id"]].items() if k != "id"}}
            keys = pages._same_item_keys(d)
            if keys & shown:
                continue
            shown.update(keys)
            got.append(d)
            if len(got) >= limit:
                break
        return got

    return pick(pages.FEED_KINDS, n), pick(pages.CHEAPER_KINDS, n)


def to_row(db: Database, i: int, d: dict, today: str) -> dict:
    since = (date.fromisoformat(today) - timedelta(days=35)).isoformat()
    feats = json.loads(d.get("features") or "{}")
    hist = series(db, d["product_id"], since)
    s30 = window_stats(hist, today, 30, exclude_today=True)
    others = links(db, d["product_id"], today)
    validated = d["kind"] in detector.VALIDATED_KINDS
    return {
        "puesto": i,
        "tienda": pages.store_name(d["store_key"]),
        "producto": d["name"],
        "clase": d["kind"],
        "estado": d["status"],
        "precio_actual": d["cur_price"],
        "precio_detectado": d["price"],
        "mediana_30d": s30.median,
        "dias_con_dato_30d": s30.days_covered,
        "referencia": d["reference"],
        "referencia_de": feats.get("reference_kind", "") + (f" ({pages.store_name(feats['peer_store'])})"
                                                           if feats.get("peer_store") else ""),
        "pct_bajo_referencia": pages._pct(d["price"], d["reference"]),
        "otras_tiendas": "; ".join(
            f"{pages.store_name(o['store_key'])} Q{o['cur_price']:,.2f} ({METHOD.get(o['method'], o['method'])} "
            f"{o['confidence']:.2f}) {o['name'][:60]}" for o in others),
        "vinculo_propio": METHOD.get(others[0]["own_method"], others[0]["own_method"]) if others else "",
        # Oferta fuerte, posible error y "más barato" pasan por la validación de
        # pares (Jev en producción) antes de publicarse; una oferta simple no.
        "pares_validados": ("sí" if validated else "no (oferta simple)") if others else "",
        "pares_rechazados": feats.get("peers_rejected", 0),
        "ficha": f"{pages.SITE_URL}/p/{d['product_id']}",
        "tienda_url": d["url"],
    }


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["puesto"])
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--simular", action="store_true", help="aplicar el detector actual sin escribir")
    ap.add_argument("--out", default=str(Path.home() / ".gt-compare" / f"revision-ofertas-{today_utc()}.csv"))
    args = ap.parse_args()

    db = open_db(None, migrate=False)
    today = today_utc()
    if args.simular:
        offers, cheaper = simulated(db, today, args.n)
    else:
        shown: set = set()
        offers = pages.feed(db, limit=args.n, shown=shown)
        cheaper = pages.feed(db, limit=args.n, kinds=pages.CHEAPER_KINDS, shown=shown)
    out = Path(args.out).expanduser()
    write(out, [to_row(db, i, d, today) for i, d in enumerate(offers, 1)])
    cheaper_out = out.with_name(out.stem + "-mas-barato" + out.suffix)
    write(cheaper_out, [to_row(db, i, d, today) for i, d in enumerate(cheaper, 1)])
    print(f"{len(offers)} ofertas en {out}")
    print(f"{len(cheaper)} 'más barato que en X' en {cheaper_out}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
