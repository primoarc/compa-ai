"""Las primeras N de /ofertas en un CSV para revisarlas a mano antes de compartir.

    python scripts/export_ofertas.py [--n 30] [--out ~/.gt-compare/revision-ofertas.csv]

Lee la base de GT_COMPARE_DB_URL (Turso) o la local. El CSV queda fuera del
repo: son datos de las tiendas y el repo es público.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare import pages  # noqa: E402
from gt_compare.db import open_db  # noqa: E402
from gt_compare.decide import schemas  # noqa: E402
from gt_compare.history import series, today_utc, window_stats  # noqa: E402

METHOD = {"ean": "EAN", "jev": "Jev", "model": "código de modelo", "manual": "manual (panel)"}


def links(db, pid: int) -> list[dict]:
    """Otras tiendas del mismo grupo, con cómo se unió cada una."""
    return db.query(
        """SELECT p.store_key, p.name, p.cur_price, pc2.method, pc2.confidence, pc1.method AS own_method
           FROM product_clusters pc1
           JOIN product_clusters pc2 ON pc2.cluster_id = pc1.cluster_id AND pc2.product_id != pc1.product_id
           JOIN products p ON p.id = pc2.product_id
           WHERE pc1.product_id = ? AND pc1.confidence >= ? AND pc2.confidence >= ?
           ORDER BY p.cur_price""",
        (pid, schemas.MATCH_ACCEPT, schemas.MATCH_ACCEPT),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", default=str(Path.home() / ".gt-compare" / f"revision-ofertas-{today_utc()}.csv"))
    args = ap.parse_args()

    db = open_db(None, migrate=False)
    today = today_utc()
    since = (date.fromisoformat(today) - timedelta(days=35)).isoformat()
    rows = []
    for i, d in enumerate(pages.feed(db, limit=args.n), 1):
        feats = json.loads(d.get("features") or "{}")
        s30 = window_stats(series(db, d["product_id"], since), today, 30, exclude_today=True)
        others = links(db, d["product_id"])
        validated = d["kind"] in ("oferta_fuerte", "posible_error")
        rejected = feats.get("peers_rejected", 0)
        rows.append({
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
            "referencia_de": feats.get("reference_kind", ""),
            "pct_bajo_referencia": pages._pct(d["price"], d["reference"]),
            "otras_tiendas": "; ".join(
                f"{pages.store_name(o['store_key'])} Q{o['cur_price']:,.2f} ({METHOD.get(o['method'], o['method'])} "
                f"{o['confidence']:.2f}) {o['name'][:60]}" for o in others),
            "vinculo_propio": METHOD.get(others[0]["own_method"], others[0]["own_method"]) if others else "",
            # Solo oferta fuerte y posible error pasan por la validación de pares
            # (Jev en producción) antes de publicarse; una oferta simple no.
            "pares_validados": ("sí" if validated else "no (oferta simple)") if others else "",
            "pares_rechazados": rejected,
            "ficha": f"{pages.SITE_URL}/p/{d['product_id']}",
            "tienda_url": d["url"],
        })
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["puesto"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} ofertas en {out}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
