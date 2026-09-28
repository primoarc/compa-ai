"""Agrupa el mismo producto entre tiendas.

1. EAN/UPC exacto (confianza 0,92: en la auditoría el EAN acertó 113 de 123).
2. Mismo código de modelo y misma marca: la regla del comparador decide. Si
   coincide sin specs contradictorias se acepta; si el código coincide pero las
   specs chocan (variante probable) la decide Jev, o va a revisión sin Jev.
3. Solo los vínculos aceptados forman grupos. Los precios se comparan entre
   tiendas únicamente dentro de un grupo (confianza >= MATCH_ACCEPT).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from . import matching
from .db import Database
from .relevance import normalize
from .decide import Decider
from .decide import schemas
from .history import now_iso

logger = logging.getLogger("gt_compare.clusters")

# Confianzas calibradas con el benchmark (docs/benchmark-jev.md): la regla de código
# de modelo acertó 2 de 3 y el EAN exacto 113 de 123. La regla queda debajo del umbral
# de comparación: agrupa para mostrar, pero no sirve de referencia de precio.
RULES_CONFIDENCE = 0.7
EAN_CONFIDENCE = 0.92
MAX_BLOCK = 12  # un código compartido por más SKUs que esto es genérico


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def candidate_pairs(rows: list[dict]) -> list[tuple[dict, dict]]:
    """Pares de tiendas distintas con el mismo código de modelo y sin EAN que ya los una."""
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["model_code"]:
            by_code[r["model_code"]].append(r)
    pairs = []
    for block in by_code.values():
        if len(block) < 2 or len(block) > MAX_BLOCK:
            continue
        for i, a in enumerate(block):
            for b in block[i + 1:]:
                if a["store_key"] == b["store_key"]:
                    continue
                if a["ean"] and b["ean"]:
                    continue  # el EAN ya decidió (igual o distinto)
                pairs.append((a, b))
    return pairs


# Candidatos por nombre para Jev: misma marca y categoría, nombres parecidos. Es
# lo que el benchmark midió (precisión 0,978); la regla de código de modelo casi
# no aplica fuera de electrónica. Las decisiones quedan en cache: un par que no
# cambió no se vuelve a pagar.
NAME_SIMILARITY = 0.5
NAME_PER_PRODUCT = 3
MAX_NAME_PAIRS = 20_000


def _brand_key(r: dict) -> str:
    if r.get("brand"):
        return normalize(r["brand"])
    found = sorted(matching.brands(r["name"] or ""))
    return found[0] if found else ""


def name_candidates(rows: list[dict], exclude: set[tuple[int, int]]) -> list[tuple[dict, dict]]:
    """Pares de tiendas distintas, misma marca y categoría, nombres con Jaccard alto."""
    blocks: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        brand = _brand_key(r)
        if brand:
            blocks[(brand, (r.get("category_id") or "").split("/")[0])].append(r)
    words = {r["id"]: {w for w in normalize(r["name"] or "").split() if len(w) > 2} for r in rows}
    scored: list[tuple[float, dict, dict]] = []
    for block in blocks.values():
        for i, a in enumerate(block):
            wa = words[a["id"]]
            for b in block[i + 1:]:
                if a["store_key"] == b["store_key"] or (a["ean"] and a["ean"] == b["ean"]):
                    continue
                key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                wb = words[b["id"]]
                if key in exclude or not wa or not wb:
                    continue
                sim = len(wa & wb) / len(wa | wb)
                if sim >= NAME_SIMILARITY:
                    scored.append((sim, a, b))
    scored.sort(key=lambda t: -t[0])
    per: dict[int, int] = defaultdict(int)
    out: list[tuple[dict, dict]] = []
    for _, a, b in scored:
        if per[a["id"]] >= NAME_PER_PRODUCT or per[b["id"]] >= NAME_PER_PRODUCT:
            continue
        per[a["id"]] += 1
        per[b["id"]] += 1
        out.append((a, b))
        if len(out) >= MAX_NAME_PAIRS:
            break
    return out


def _side(r: dict) -> dict:
    return {"name": r["name"], "brand": r.get("brand"), "category": r.get("store_category")}


async def rebuild(db: Database, decider: Optional[Decider] = None) -> dict:
    """Recalcula grupos con los productos vistos en los últimos 7 días."""
    rows = db.query(
        """SELECT id, store_key, name, brand, ean, model_code, store_category, category_id FROM products p
           WHERE sku_level=1 AND p.id IN (SELECT product_id FROM price_history WHERE end_day >= date('now', '-7 day'))"""
    )
    uf = _UnionFind()
    method: dict[int, tuple[str, float]] = {}
    by_id_all = {r["id"] for r in rows}

    by_ean: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["ean"]:
            by_ean[r["ean"]].append(r)
    for group in by_ean.values():
        if len({r["store_key"] for r in group}) < 2:
            continue
        for r in group[1:]:
            uf.union(group[0]["id"], r["id"])
        for r in group:
            method[r["id"]] = ("ean", EAN_CONFIDENCE)

    pairs = candidate_pairs(rows)
    rule_decs = [schemas.match_rules(schemas.match_state(_side(a), _side(b))) for a, b in pairs]
    ambiguous = [(p, d) for p, d in zip(pairs, rule_decs) if d.value == 1]
    name_pairs: list[tuple[dict, dict]] = []
    if decider is not None and decider.enabled:
        seen_pairs = {(min(a["id"], b["id"]), max(a["id"], b["id"])) for a, b in pairs}
        name_pairs = name_candidates(rows, seen_pairs)
        ambiguous += [(p, schemas.match_rules(schemas.match_state(_side(p[0]), _side(p[1]))))
                      for p in name_pairs]
    stats = {"ean_groups": sum(1 for g in by_ean.values() if len({r['store_key'] for r in g}) > 1),
             "pairs": len(pairs), "name_pairs": len(name_pairs), "rules_accept": 0, "jev_accept": 0,
             "review": 0}

    for (a, b), dec in zip(pairs, rule_decs):
        if dec.value == 2:
            uf.union(a["id"], b["id"])
            for r in (a, b):
                method.setdefault(r["id"], ("model", RULES_CONFIDENCE))
            stats["rules_accept"] += 1

    # Lo que se decidió a mano en el panel manda sobre reglas y Jev.
    manual = {(r["product_a"], r["product_b"]): r["status"] for r in db.query(
        "SELECT product_a, product_b, status FROM match_reviews WHERE status IN ('accepted','rejected')")}
    for (a_id, b_id), status in manual.items():
        if status == "accepted" and a_id in by_id_all and b_id in by_id_all:
            uf.union(a_id, b_id)
            for x in (a_id, b_id):
                method[x] = ("manual", 1.0)

    reviews = []
    if ambiguous:
        if decider is not None and decider.enabled:
            states = [schemas.match_state(_side(a), _side(b)) for (a, b), _ in ambiguous]
            decs = await decider.decide_many(schemas.MATCH, states, schemas.match_rules)
        else:
            decs = [d for _, d in ambiguous]
        for ((a, b), _), dec in zip(ambiguous, decs):
            if (min(a["id"], b["id"]), max(a["id"], b["id"])) in manual:
                continue
            outcome = schemas.match_outcome(dec) if dec.source != "rules" else "review"
            p = schemas.p_same(dec) if dec.source != "rules" else None
            if outcome == "accept":
                uf.union(a["id"], b["id"])
                for r in (a, b):
                    method.setdefault(r["id"], ("jev", p or 0.0))
                stats["jev_accept"] += 1
            elif outcome == "review":
                reviews.append((min(a["id"], b["id"]), max(a["id"], b["id"]), p, dec.value, dec.source))
                stats["review"] += 1

    groups: dict[int, list[int]] = defaultdict(list)
    for pid in method:
        groups[uf.find(pid)].append(pid)
    by_id = {r["id"]: r for r in rows}
    now = now_iso()
    # El id del grupo es el menor product_id: estable entre corridas, así se
    # escribe solo la diferencia (en Turso cada fila escrita cuenta).
    want_clusters: dict[int, dict] = {}
    want_links: dict[int, tuple] = {}
    for members in groups.values():
        if len({by_id[m]["store_key"] for m in members}) < 2:
            continue
        cid = min(members)
        want_clusters[cid] = by_id[cid]
        for m in members:
            how, conf = method[m]
            want_links[m] = (cid, how, round(conf, 4))
    have_links = {r["product_id"]: (r["cluster_id"], r["method"], round(r["confidence"] or 0, 4))
                  for r in db.query("SELECT product_id, cluster_id, method, confidence FROM product_clusters")}
    have_clusters = {r["id"] for r in db.query("SELECT id FROM clusters")}
    with db.transaction():
        gone = [(pid,) for pid in have_links if pid not in want_links]
        if gone:
            db.executemany("DELETE FROM product_clusters WHERE product_id=?", gone)
        old = [(cid,) for cid in have_clusters - set(want_clusters)]
        if old:
            db.executemany("DELETE FROM clusters WHERE id=?", old)
        new = [(cid, r["ean"], r["name"], r["brand"]) for cid, r in want_clusters.items()
               if cid not in have_clusters]
        if new:
            db.executemany("INSERT INTO clusters (id, ean, name, brand, category_id) VALUES (?,?,?,?,NULL)", new)
        changed = [(pid, cid, how, conf, now) for pid, (cid, how, conf) in want_links.items()
                   if have_links.get(pid) != (cid, how, conf)]
        if changed:
            db.executemany(
                """INSERT INTO product_clusters (product_id, cluster_id, method, confidence, decided_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(product_id) DO UPDATE SET cluster_id=excluded.cluster_id,
                     method=excluded.method, confidence=excluded.confidence, decided_at=excluded.decided_at""",
                changed,
            )
        if reviews:
            # Solo se escribe una revisión nueva o con otra probabilidad.
            db.executemany(
                """INSERT INTO match_reviews (product_a, product_b, p_same, level, source, created_at)
                   VALUES (?,?,?,?,?,datetime('now'))
                   ON CONFLICT(product_a, product_b) DO UPDATE SET
                     p_same=excluded.p_same, level=excluded.level, source=excluded.source
                   WHERE match_reviews.p_same IS NOT excluded.p_same
                      OR match_reviews.level IS NOT excluded.level""",
                reviews,
            )
    stats["clusters"] = len(want_clusters)
    stats["linked_products"] = len(want_links)
    stats["link_writes"] = len(gone) + len(old) + len(new) + len(changed)
    logger.info("grupos: %s", stats)
    return stats


def peers(db: Database, product_id: int, min_confidence: float = schemas.MATCH_ACCEPT) -> list[dict]:
    """Mismo producto en otras tiendas, solo con vínculo de confianza alta."""
    return db.query(
        """SELECT p.id, p.store_key, p.name, p.url, p.cur_price, p.cur_available, pc2.confidence
           FROM product_clusters pc1
           JOIN product_clusters pc2 ON pc2.cluster_id = pc1.cluster_id AND pc2.product_id != pc1.product_id
           JOIN products p ON p.id = pc2.product_id
           WHERE pc1.product_id = ? AND pc1.confidence >= ? AND pc2.confidence >= ?
           ORDER BY p.cur_price""",
        (product_id, min_confidence, min_confidence),
    )
