"""Benchmark de decisiones: reglas actuales contra Jev.

    python scripts/benchmark_jev.py build     # arma los sets desde el historial
    python scripts/benchmark_jev.py run       # corre reglas y, si hay key, Jev
    python scripts/benchmark_jev.py run --rules-only   # sin tocar la key

Los sets salen del catálogo de las tiendas, así que se guardan fuera del repo
(~/.gt-compare/bench). Ground truth:
  - matching: mismo EAN en tiendas distintas = mismo producto; misma marca y
    nombre parecido con EAN distinto = distinto (negativos difíciles).
  - categoría: ruta de categoría de la tienda (VTEX) traducida a mano a la
    taxonomía propia. Ni las reglas ni Jev ven esa ruta: solo nombre y marca.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare.db import open_db  # noqa: E402
from gt_compare.decide import Decider  # noqa: E402
from gt_compare.decide import core, schemas, taxonomy  # noqa: E402
from gt_compare.relevance import normalize  # noqa: E402

OUT = Path.home() / ".gt-compare" / "bench"
SEED = 7

# Ruta de categoría de tienda (normalizada, primer o segundo nivel) -> nivel 1 propio.
# Solo rutas inequívocas; lo demás no entra al set.
PATH_TO_L1 = [
    (r"/(televisores?|tv y video|pantallas)/", "televisores_video"),
    (r"/(audio|audifonos|bocinas)/", "audio"),
    (r"/(celulares|telefonia|smartphones|tablets)/", "celulares_tablets"),
    (r"/(computacion|computadoras|laptops|impresoras)/", "computacion"),
    (r"/(videojuegos|gaming|consolas)/", "videojuegos"),
    (r"/(linea blanca|refrigeracion|lavado)/", "linea_blanca"),
    (r"/(electrodomesticos|pequenos electrodomesticos|electrodomesticos de cocina)/", "electrodomesticos_pequenos"),
    (r"/(muebles|colchones|dormitorio|decoracion|iluminacion)/", "hogar_muebles"),
    (r"/(cocina|mesa y cocina|articulos de cocina)/", "cocina_mesa"),
    (r"/(ferreteria|herramientas|pinturas?|jardin|construccion)/", "ferreteria_herramientas"),
    (r"/(ropa|moda|calzado|zapatos|accesorios de moda|relojes)/", "moda_accesorios"),
    (r"/(belleza|cuidado personal|farmacia|salud|perfumeria|maquillaje)/", "belleza_salud"),
    (r"/(juguetes|juguetería|jugueteria|bebes|bebe)/", "bebes_juguetes"),
    (r"/(deportes|fitness|aire libre|camping)/", "deportes_aire_libre"),
    (r"/(abarrotes|alimentos|bebidas|limpieza|mascotas|lacteos|carnes|snacks|licores)/", "supermercado"),
    (r"/(automotriz|autos|llantas)/", "automotriz"),
]
_PATH_RES = [(re.compile(p), l1) for p, l1 in PATH_TO_L1]


def path_l1(path: str) -> str | None:
    text = "/" + "/".join(normalize(p) for p in (path or "").strip("/").split("/")[:2]) + "/"
    hits = {l1 for rx, l1 in _PATH_RES if rx.search(text)}
    return hits.pop() if len(hits) == 1 else None


def _tokens(name: str) -> set[str]:
    return {t for t in normalize(name).split() if len(t) > 2}


def build(force: bool = False) -> None:
    if (OUT / "match.json").exists() and not force:
        # match.json guarda la auditoría manual de los 300 pares: no se pisa sin querer.
        sys.exit("Los sets ya existen (con auditoría manual). Usar `build --force` para rehacerlos.")
    db = open_db()
    rnd = random.Random(SEED)
    rows = db.query(
        """SELECT id, store_key, name, brand, ean, store_category FROM products p
           WHERE sku_level=1 AND p.id IN (SELECT product_id FROM price_history WHERE end_day >= date('now','-3 day'))"""
    )
    by_ean: dict[str, list[dict]] = {}
    for r in rows:
        if r["ean"]:
            by_ean.setdefault(r["ean"], []).append(r)
    positives = []
    for group in by_ean.values():
        stores = {}
        for r in group:
            stores.setdefault(r["store_key"], r)
        if len(stores) >= 2:
            a, b = rnd.sample(list(stores.values()), 2)
            positives.append((a, b))
    rnd.shuffle(positives)

    # Negativos difíciles: misma marca, EAN distinto, tiendas distintas, nombres parecidos.
    by_brand: dict[str, list[dict]] = {}
    for r in rows:
        if r["ean"] and r["brand"]:
            by_brand.setdefault(normalize(r["brand"]), []).append(r)
    negatives = []
    for group in by_brand.values():
        if len(group) < 2:
            continue
        rnd.shuffle(group)
        group = group[:60]  # tope por marca: marcas enormes no dominan el set
        toks = [_tokens(r["name"]) for r in group]
        best: list[tuple[float, dict, dict]] = []
        for i, a in enumerate(group):
            for j in range(i + 1, len(group)):
                b = group[j]
                if a["store_key"] == b["store_key"] or a["ean"] == b["ean"]:
                    continue
                ta, tb = toks[i], toks[j]
                if ta and tb:
                    sim = len(ta & tb) / len(ta | tb)
                    if sim >= 0.35:
                        best.append((sim, a, b))
        best.sort(key=lambda t: -t[0])
        negatives += [(a, b) for _, a, b in best[:3]]  # los 3 más parecidos por marca
    rnd.shuffle(negatives)
    n = min(len(positives), len(negatives), 150)
    pairs = [{"a": a, "b": b, "same": True} for a, b in positives[:n]] + \
            [{"a": a, "b": b, "same": False} for a, b in negatives[:n]]
    rnd.shuffle(pairs)

    cats = []
    for r in rows:
        if r["store_key"] in ("walmart", "siman", "cemaco") and r["store_category"]:
            l1 = path_l1(r["store_category"])
            if l1:
                cats.append({"id": r["id"], "name": r["name"], "brand": r["brand"],
                             "path": r["store_category"], "l1": l1})
    # Muestra estratificada: hasta 20 por categoría para que ninguna domine.
    by_l1: dict[str, list[dict]] = {}
    for c in cats:
        by_l1.setdefault(c["l1"], []).append(c)
    sample = []
    for items in by_l1.values():
        rnd.shuffle(items)
        sample += items[:20]
    rnd.shuffle(sample)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "match.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    (OUT / "category.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1))
    print(f"matching: {len(pairs)} pares ({n} mismos, {n} distintos) de {len(positives)} posibles positivos")
    print(f"categoría: {len(sample)} productos en {len(by_l1)} categorías")


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def _side(p: dict) -> dict:
    return {"name": p["name"], "brand": p.get("brand")}


async def run(rules_only: bool = False) -> None:
    pairs = json.loads((OUT / "match.json").read_text())
    cats = json.loads((OUT / "category.json").read_text())
    results: dict = {"n_pairs": len(pairs), "n_products": len(cats), "model": core.JEV_MODEL}

    # Reglas
    t0 = time.perf_counter()
    rule_match = [schemas.match_rules(schemas.match_state(_side(p["a"]), _side(p["b"]))) for p in pairs]
    t_match = (time.perf_counter() - t0) * 1000 / len(pairs)
    t0 = time.perf_counter()
    rule_cat = [taxonomy.classify_rules(c["name"])[0] for c in cats]
    t_cat = (time.perf_counter() - t0) * 1000 / len(cats)
    audited = [p for p in pairs if p.get("audit", "?") != "?"]
    same_ean = [p for p in audited if p["same"]]
    results["ean_exact_vs_audit"] = {
        "same_ean_pairs": len(same_ean),
        "precision": round(sum(p["audit"] == "S" for p in same_ean) / len(same_ean), 3) if same_ean else None,
        "missed_same_with_different_ean": sum(1 for p in audited if not p["same"] and p["audit"] == "S"),
    }
    results["rules"] = {
        "match": _match_metrics(pairs, [schemas.match_outcome(d) for d in rule_match]),
        "match_ms_per_decision": round(t_match, 3),
        "category": _cat_metrics(cats, rule_cat),
        "category_ms_per_decision": round(t_cat, 3),
    }

    decider = Decider(client=None) if rules_only else Decider(enabled=True)
    if not decider.enabled:
        results["jev"] = None
        print("sin TYPESAFE_API_KEY: solo reglas")
    else:
        results["jev"] = {}
        for name, schema, states, scorer in (
            ("match", schemas.MATCH,
             [schemas.match_state(_side(p["a"]), _side(p["b"])) for p in pairs],
             lambda decs: _match_metrics(pairs, [schemas.match_outcome(d) for d in decs],
                                         [schemas.p_same(d) for d in decs])),
            ("category", schemas.CATEGORY_L1,
             [schemas.category_state(c["name"], c["brand"]) for c in cats],
             lambda decs: _cat_metrics(cats, [d.value for d in decs],
                                       [d.confidence for d in decs])),
        ):
            before = decider.client.input_tokens  # type: ignore[union-attr]
            started = time.perf_counter()
            decs = await decider.decide_many(schema, states, _fail_fallback, concurrency=core.BATCH_CONCURRENCY)
            wall = time.perf_counter() - started
            tokens = decider.client.input_tokens - before  # type: ignore[union-attr]
            lat = [d.latency_ms for d in decs if d.source == "jev"]
            ok = sum(1 for d in decs if d.source == "jev")
            results["jev"][name] = {
                **scorer(decs),
                "answered": ok, "fallbacks": len(decs) - ok,
                "input_tokens": tokens,
                "usd_per_1000": round(tokens / max(ok, 1) * 1000 / 1e6 * core.JEV_PRICE_PER_MTOK, 5),
                "p50_ms": round(statistics.median(lat), 1) if lat else None,
                "p95_ms": round(_pct(lat, 0.95), 1) if lat else None,
                "wall_s": round(wall, 1),
            }
        # Intención en tiempo real: latencia con el timeout de producción.
        queries = ["tele 55 barata", "refri samsung", "compu gamer", "audifonos sony", "licuadora oster",
                   "iphone 15", "colchon queen", "taladro dewalt", "freidora de aire", "ps5",
                   "lavadora 20 libras", "monitor 27", "cafetera nespresso", "aire acondicionado 12000",
                   "bocina jbl", "laptop i5 barata", "tablet para niños", "silla gamer", "microondas", "smart watch"]
        lat = []
        timeouts = 0
        for q in queries:
            d = await decider.decide(schemas.INTENT, schemas.intent_state(q), schemas.intent_rules,
                                     timeout=None, use_cache=False)
            if d.source == "jev":
                lat.append(d.latency_ms)
                timeouts += d.latency_ms > schemas.INTENT_TIMEOUT * 1000
        results["jev"]["intent_latency"] = {
            "n": len(queries), "p50_ms": round(statistics.median(lat), 1) if lat else None,
            "p95_ms": round(_pct(lat, 0.95), 1) if lat else None,
            "over_timeout": timeouts,
        }
        await decider.aclose()
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(json.dumps(results, ensure_ascii=False, indent=1))


def _fail_fallback(state) -> core.Decision:
    return core.Decision(None, None, source="rules")


def _truth(p: dict, label: str):
    """Verdad según el EAN crudo o según la auditoría manual (None si no se pudo decidir)."""
    if label == "ean":
        return p["same"]
    return None if p.get("audit", "?") == "?" else p["audit"] == "S"


def _match_metrics(pairs: list[dict], outcomes: list[str], probs: list | None = None) -> dict:
    out = {}
    for label in ("ean", "audit"):
        rows = [(t, o, q) for p, o, q in zip(pairs, outcomes, probs or [None] * len(pairs))
                if (t := _truth(p, label)) is not None]
        tp = sum(1 for t, o, _ in rows if o == "accept" and t)
        fp = sum(1 for t, o, _ in rows if o == "accept" and not t)
        fn = sum(1 for t, o, _ in rows if o != "accept" and t)
        m = {
            "n": len(rows), "accepted": tp + fp,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "false_merges": fp, "review": sum(1 for _, o, _ in rows if o == "review"),
        }
        if probs:
            m["accuracy_at_0_5"] = round(sum(1 for t, _, q in rows if q is not None and (q >= 0.5) == t)
                                         / len(rows), 3)
        out[label] = m
    return out


def _cat_metrics(cats: list[dict], preds: list, confs: list | None = None) -> dict:
    correct = [c["l1"] == p for c, p in zip(cats, preds)]
    classified = [ok for ok, p in zip(correct, preds) if p not in (None, taxonomy.OTHER)]
    out = {"accuracy": round(sum(correct) / len(cats), 3),
           "unclassified": len(cats) - len(classified),
           "accuracy_when_classified": round(sum(classified) / len(classified), 3) if classified else None}
    if confs:
        hi = [(ok, c) for ok, c in zip(correct, confs) if c is not None and c >= schemas.CATEGORY_ACCEPT]
        out["accepted_at_conf"] = len(hi)
        out["accuracy_when_accepted"] = round(sum(ok for ok, _ in hi) / len(hi), 3) if hi else None
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "build":
        build(force="--force" in sys.argv)
    else:
        asyncio.run(run(rules_only="--rules-only" in sys.argv))
