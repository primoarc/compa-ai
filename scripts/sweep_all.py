#!/usr/bin/env python3
"""Barrido de catálogo completo de las tiendas VTEX, para correr desde cron.

Guarda el catálogo entero en ~/.gt-compare/snapshots/{tienda}-{fecha}.json e
imprime SOLO las anomalías. El snapshot en disco es la base del historial: con
dos barridos de días distintos ya se puede comparar cada producto contra su
propio pasado, que es lo que el precio de lista no puede darnos.

Uso:  python scripts/sweep_all.py [tienda ...]
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gt_compare import sweep  # noqa: E402
from gt_compare.stores import load_stores  # noqa: E402

SNAP_DIR = Path.home() / ".gt-compare" / "snapshots"
DEFAULT = ["siman", "cemaco", "walmart"]


async def una(store) -> None:
    productos, stats = await sweep.sweep_store(store)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    destino = SNAP_DIR / f"{store.key}-{date.today():%Y%m%d}.json"
    destino.write_text(
        json.dumps(
            [
                {
                    "name": p.name, "price": p.price, "list_price": p.list_price,
                    "available": p.available, "url": p.url,
                }
                for p in productos
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    anomalias = sweep.anomalias_por_lista(productos)
    print(
        f"[{store.key}] {stats.categorias} cortes · {stats.paginas} páginas · "
        f"{stats.productos} productos · {stats.errores} errores · "
        f"{len(anomalias)} anomalías · snapshot {destino.name}"
    )
    for d, p in anomalias:
        print(
            f"   -{d*100:3.0f}%  Q{p.price:>9,.0f} (lista Q{p.list_price:>9,.0f})  "
            f"disp={p.available:<4} {p.name[:60]}"
        )
        print(f"          {p.url}")


async def main() -> None:
    pedidas = sys.argv[1:] or DEFAULT
    tiendas = {s.key: s for s in load_stores()}
    for k in pedidas:
        s = tiendas.get(k)
        if not s or s.kind != "vtex":
            print(f"[{k}] no es una tienda VTEX enumerable", file=sys.stderr)
            continue
        try:
            await una(s)          # de a una: no le pegamos a dos tiendas a la vez
        except Exception as exc:  # noqa: BLE001
            print(f"[{k}] error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
