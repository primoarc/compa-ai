"""Asigna la categoría propia (nivel1/nivel2) a los productos que no la tienen.

Reglas primero. Solo lo que las reglas no ubican con una palabra clave clara va
a Jev (nivel 1 y después nivel 2), y solo si `use_jev` y el benchmark lo
justificó. Sin Jev queda la mejor suposición de las reglas con confianza baja.
"""

from __future__ import annotations

import logging
from typing import Optional

from .db import Database
from .decide import Decider
from .decide import schemas, taxonomy

logger = logging.getLogger("gt_compare.categorize")

RULES_CLEAR = 6.0  # peso mínimo (largo de la palabra clave) para confiar en la regla
# Productos con suposición débil de reglas que pasan a Jev por corrida: ~156 k
# pendientes el 28-sep, US$0,035 por 1.000 → ~US$0,70 por corrida, ~8 corridas.
JEV_BATCH = 20_000


def category_id(l1: str, l2: Optional[str]) -> str:
    return f"{l1}/{l2}" if l2 else l1


async def run(db: Database, decider: Optional[Decider] = None, *, use_jev: bool = False,
              limit: int = 0) -> dict:
    rows = db.query(
        "SELECT id, name, brand, store_category FROM products WHERE category_id IS NULL"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    retried: list[dict] = []
    if use_jev and decider is not None and decider.enabled:
        # Las suposiciones débiles de corridas anteriores también van a Jev.
        retried = db.query(
            "SELECT id, name, brand, store_category FROM products WHERE category_source='rules_low' "
            "ORDER BY id LIMIT ?", (JEV_BATCH,))
        rows += retried
    updates: list[tuple] = []
    unclear: list[dict] = []
    for r in rows:
        l1, l2, weight = taxonomy.classify_rules(r["name"], r["store_category"])
        if weight >= RULES_CLEAR:
            updates.append((category_id(l1, l2), "rules", 0.9, r["id"]))
        else:
            unclear.append({**r, "_rules": (l1, l2, weight)})

    if unclear and use_jev and decider is not None and decider.enabled:
        states = [schemas.category_state(r["name"], r["brand"], r["store_category"]) for r in unclear]
        # Un producto categorizado no se vuelve a preguntar: la decisión no se guarda.
        l1s = await decider.decide_many(schemas.CATEGORY_L1, states, schemas.category_l1_rules,
                                        persist=False)
        by_parent: dict[str, list[int]] = {}
        for i, dec in enumerate(l1s):
            by_parent.setdefault(dec.value, []).append(i)
        l2s: dict[int, Optional[str]] = {}
        for parent, idxs in by_parent.items():
            sch = schemas.category_l2_schema(parent)
            if sch is None:
                continue
            decs = await decider.decide_many(sch, [states[i] for i in idxs], schemas.category_l2_rules,
                                             persist=False)
            for i, dec in zip(idxs, decs):
                l2s[i] = dec.value if dec.value in taxonomy.level2_options(parent) else None
        for i, (r, dec) in enumerate(zip(unclear, l1s)):
            conf = dec.confidence if dec.confidence is not None else 0.0
            if dec.source != "rules" and conf >= schemas.CATEGORY_ACCEPT:
                updates.append((category_id(dec.value, l2s.get(i)), "jev", conf, r["id"]))
            else:
                # Jev tampoco la ubicó con confianza: no vuelve a la cola.
                l1, l2, _ = r["_rules"]
                source = "jev_low" if dec.source != "rules" else "rules_low"
                updates.append((category_id(l1, l2), source, 0.3, r["id"]))
    else:
        for r in unclear:
            l1, l2, _ = r["_rules"]
            updates.append((category_id(l1, l2), "rules_low", 0.3, r["id"]))

    # Una suposición débil que sigue igual (Jev caído) no se reescribe.
    retry_ids = {r["id"] for r in rows[len(rows) - len(retried):]} if retried else set()
    updates = [u for u in updates if not (u[1] == "rules_low" and u[3] in retry_ids)]
    db.executemany(
        "UPDATE products SET category_id=?, category_source=?, category_conf=? WHERE id=?", updates
    )
    out: dict = {"products": len(rows)}
    for u in updates:
        out[u[1]] = out.get(u[1], 0) + 1
    logger.info("categorías: %s", out)
    return out
