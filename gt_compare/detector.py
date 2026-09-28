"""Detector de ofertas y posibles price errors sobre el historial.

Señales (todas calculadas en código, nunca por un modelo):
  - caída contra la mediana de 30 días (sin contar hoy),
  - caída contra el mismo producto en otras tiendas (solo vínculos de confianza alta),
  - caída súbita contra el intervalo anterior,
  - descuento real contra el "precio antes" que muestra la tienda.
La referencia es la menor entre la mediana de 30 días y la de 90: un producto
que vuelve a su precio normal después de un alza no cuenta como oferta.

Clases: oferta (>=15%), oferta_fuerte (>=30%), posible_error (>=50% y caída
súbita o confirmada por otras tiendas). Aparte, descuento_falso: el precio de
lista subió >=20% en las dos semanas previas y el precio no bajó de lo normal.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Optional

from .db import Database
from .decide import Decider
from .decide import schemas
from .history import GAP_DAYS, previous_price, window_stats
from .products import MAX_PLAUSIBLE_PRICE

logger = logging.getLogger("gt_compare.detector")

OFERTA = 0.15
OFERTA_FUERTE = 0.30
POSIBLE_ERROR = 0.50
SUDDEN = 0.40
MIN_HISTORY_DAYS = 7
MIN_REFERENCE = 50.0  # Q: por debajo no vale la pena publicarlo
FAKE_LIST_RISE = 0.20
FAKE_WINDOW = 14


@dataclass
class Features:
    price: float
    list_price: Optional[float]
    median30: Optional[float]
    median90: Optional[float]
    min30: Optional[float]
    days30: int
    days90: int
    previous: Optional[float]
    peers_median: Optional[float]
    peers: int
    list_14d_ago: Optional[float]

    @property
    def reference(self) -> tuple[Optional[float], str]:
        """(precio de referencia, de dónde sale)."""
        own = None
        if self.days30 >= MIN_HISTORY_DAYS and self.median30:
            own = min(self.median30, self.median90 or self.median30)
        elif self.days90 >= MIN_HISTORY_DAYS and self.median90:
            own = self.median90  # p. ej. vuelve después de un mes agotado
        if own and self.peers_median:
            return (own, "historial") if own >= self.peers_median else (self.peers_median, "otras_tiendas")
        if own:
            return own, "historial"
        if self.peers_median:
            return self.peers_median, "otras_tiendas"
        return None, ""

    def drop(self, ref: Optional[float]) -> float:
        return max(0.0, 1 - self.price / ref) if ref else 0.0


@dataclass
class Verdict:
    kind: Optional[str]  # oferta | oferta_fuerte | posible_error | descuento_falso | None
    score: float
    reference: Optional[float]
    reference_kind: str
    drop: float
    signals: dict = field(default_factory=dict)


def classify(f: Features) -> Verdict:
    ref, ref_kind = f.reference
    drop = f.drop(ref)
    sudden = f.drop(f.previous)
    vs_peers = f.drop(f.peers_median)
    claimed = f.drop(f.list_price) if f.list_price and f.list_price > f.price else 0.0
    signals = {"drop_ref": round(drop, 3), "sudden": round(sudden, 3),
               "vs_peers": round(vs_peers, 3), "claimed": round(claimed, 3),
               "days30": f.days30, "peers": f.peers}

    # Descuento falso: "antes" inflado hace poco y el precio no bajó de lo normal.
    if (claimed >= OFERTA and f.list_14d_ago and f.list_price
            and f.list_price >= f.list_14d_ago * (1 + FAKE_LIST_RISE)
            and (f.median30 is None or f.price >= f.median30 * 0.97)):
        return Verdict("descuento_falso", round(claimed * 100, 1), ref, ref_kind, drop, signals)

    if ref is None or ref < MIN_REFERENCE or drop < OFERTA:
        return Verdict(None, 0.0, ref, ref_kind, drop, signals)

    # Puntaje para ordenar el feed: caída real, más peso si la confirman otras
    # tiendas o si fue súbita, y el ahorro en quetzales (log) para que una tele
    # con Q5.000 menos le gane a unos aretes con Q99 menos. El "antes" no suma.
    savings = ref - f.price
    score = (drop * 100 + 10 * min(vs_peers, drop) + 5 * min(sudden, drop)
             + 8 * math.log10(max(savings, 1.0)))
    if drop >= POSIBLE_ERROR and (sudden >= SUDDEN or vs_peers >= SUDDEN):
        kind = "posible_error"
    elif drop >= OFERTA_FUERTE:
        kind = "oferta_fuerte"
    else:
        kind = "oferta"
    return Verdict(kind, round(score, 1), ref, ref_kind, drop, signals)


def _list_price_on(intervals: list[dict], day: str) -> Optional[float]:
    for row in intervals:
        if row["start_day"] <= day <= row["end_day"]:
            return row.get("list_price")
    return None


def features_for(product: dict, intervals: list[dict], peer_prices: list[float], today: str) -> Features:
    s30 = window_stats(intervals, today, 30, exclude_today=True)
    s90 = window_stats(intervals, today, 90, exclude_today=True)
    prev = None
    current = [r for r in intervals if r["start_day"] <= today <= r["end_day"]]
    if current:
        before = [r for r in intervals if r["end_day"] < current[0]["start_day"]]
        if before and (date.fromisoformat(current[0]["start_day"])
                       - date.fromisoformat(before[-1]["end_day"])).days <= GAP_DAYS + 1:
            prev = previous_price(intervals, today)
    ago = (date.fromisoformat(today) - timedelta(days=FAKE_WINDOW)).isoformat()
    return Features(
        price=float(product["cur_price"]),
        list_price=product.get("cur_list_price"),
        median30=s30.median, median90=s90.median, min30=s30.minimum, days30=s30.days_covered, days90=s90.days_covered,
        previous=prev,
        peers_median=median(peer_prices) if peer_prices else None,
        peers=len(peer_prices),
        list_14d_ago=_list_price_on(intervals, ago),
    )


def _load(db: Database, today: str) -> tuple[list[dict], dict[int, list[dict]], dict[int, list[dict]]]:
    products = db.query(
        """SELECT p.*, pc.cluster_id FROM products p
           LEFT JOIN product_clusters pc ON pc.product_id = p.id AND pc.confidence >= ?
           WHERE p.id IN (SELECT product_id FROM price_history WHERE end_day = ?)
             AND COALESCE(p.cur_available, 1) > 0 AND p.cur_price > 0
             AND p.cur_price < ? AND p.sku_level = 1""",
        (schemas.MATCH_ACCEPT, today, MAX_PLAUSIBLE_PRICE),
    )
    ids = [p["id"] for p in products]
    since = (date.fromisoformat(today) - timedelta(days=95)).isoformat()
    intervals: dict[int, list[dict]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        for row in db.query(
            f"""SELECT * FROM price_history WHERE product_id IN ({marks}) AND end_day >= ?
                ORDER BY product_id, start_day""",
            [*chunk, since],
        ):
            intervals.setdefault(row["product_id"], []).append(row)
    by_cluster: dict[int, list[dict]] = {}
    for p in products:
        if p["cluster_id"]:
            by_cluster.setdefault(p["cluster_id"], []).append(p)
    peers: dict[int, list[dict]] = {}
    for members in by_cluster.values():
        for p in members:
            peers[p["id"]] = [q for q in members if q["store_key"] != p["store_key"]]
    return products, intervals, peers


VALIDATED_KINDS = ("oferta_fuerte", "posible_error")


async def _validate_peers(pairs: list[tuple[dict, dict]], decider: Optional[Decider]) -> list[bool]:
    """¿Cada par (producto, otra tienda) es de verdad el mismo producto?

    Con Jev encendido usa el question set de matching; si no, la regla que
    busca contradicciones entre nombre y EAN.
    """
    states = [schemas.match_state(_side(a), _side(b)) for a, b in pairs]
    if decider is not None:
        decs = await decider.decide_many(schemas.MATCH, states, schemas.ean_pair_rules)
    else:
        decs = [schemas.ean_pair_rules(s) for s in states]
    return [schemas.match_outcome(d) == "accept" for d in decs]


def _side(p: dict) -> dict:
    return {"name": p["name"], "brand": p.get("brand"), "category": p.get("store_category")}


async def run(db: Database, today: str, decider: Optional[Decider] = None,
              match_decider: Optional[Decider] = None) -> dict:
    """Detecta y guarda las ofertas del día. Los posibles errores pasan por el
    filtro de causa; solo 'error_real' con probabilidad alta queda pendiente de
    aprobación. Todo lo demás de ese grupo queda descartado con su causa."""
    products, intervals, peers = _load(db, today)

    def verdict(p: dict, peer_rows: list[dict]) -> tuple[Verdict, Features]:
        f = features_for(p, intervals.get(p["id"], []), [float(q["cur_price"]) for q in peer_rows], today)
        return classify(f), f

    first = [(p, *verdict(p, peers.get(p["id"], []))) for p in products]
    # Una oferta fuerte o un posible error que se apoya en otras tiendas se
    # sostiene solo con pares validados: el EAN compartido no alcanza.
    to_check = [(p, q) for p, v, _ in first if v.kind in VALIDATED_KINDS for q in peers.get(p["id"], [])]
    valid = dict(zip(((p["id"], q["id"]) for p, q in to_check),
                     await _validate_peers(to_check, match_decider)))
    found: list[tuple[dict, Verdict]] = []
    previous: dict[int, Optional[float]] = {}
    dropped = 0
    for p, v, f in first:
        if v.kind in VALIDATED_KINDS and peers.get(p["id"]):
            kept = [q for q in peers[p["id"]] if valid[(p["id"], q["id"])]]
            if len(kept) < len(peers[p["id"]]):
                old = v.kind
                v, f = verdict(p, kept)
                v.signals["peers_rejected"] = len(peers[p["id"]]) - len(kept)
                dropped += old != v.kind
        if v.kind:
            found.append((p, v))
            previous[p["id"]] = f.previous

    errors = [(p, v) for p, v in found if v.kind == "posible_error"]
    causes = {}
    if errors:
        states = [schemas.price_cause_state(
            name=p["name"], store=p["store_key"], price=float(p["cur_price"]),
            reference=v.reference or 0.0, reference_kind=v.reference_kind, drop_pct=v.drop,
            list_price=p.get("cur_list_price"), promo_text=p.get("promo_text"),
            other_stores=[{"price_gtq": float(q["cur_price"])} for q in peers.get(p["id"], [])],
            previous_price=previous.get(p["id"]),
        ) for p, v in errors]
        dec = (await decider.decide_many(schemas.PRICE_CAUSE, states, schemas.price_cause_rules)
               if decider else [schemas.price_cause_rules(s) for s in states])
        causes = {p["id"]: d for (p, _), d in zip(errors, dec)}

    rows = []
    counts: dict[str, int] = {}
    for p, v in found:
        cause = causes.get(p["id"])
        if v.kind == "posible_error":
            status = "pending" if cause and schemas.cause_accepts(cause) else "discarded"
        elif v.kind == "descuento_falso":
            status = "flagged"
        else:
            status = "published"
        counts[f"{v.kind}:{status}"] = counts.get(f"{v.kind}:{status}", 0) + 1
        rows.append((
            p["id"], today, v.kind, v.score, float(p["cur_price"]), v.reference,
            json.dumps({**v.signals, "reference_kind": v.reference_kind}),
            cause.value if cause else None, cause.probability if cause else None,
            cause.source if cause else None, status,
        ))
    with db.transaction():
        # No pisa decisiones ya tomadas en el panel.
        db.executemany(
            """INSERT INTO deals (product_id, detected_on, kind, score, price, reference, features,
                                  cause, cause_prob, cause_source, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id, detected_on) DO UPDATE SET
                 kind=excluded.kind, score=excluded.score, price=excluded.price,
                 reference=excluded.reference, features=excluded.features,
                 cause=excluded.cause, cause_prob=excluded.cause_prob,
                 cause_source=excluded.cause_source,
                 status=CASE WHEN deals.status IN ('approved','rejected') THEN deals.status
                             ELSE excluded.status END""",
            rows,
        )
    logger.info("detector %s: %s productos, %s, %s bajaron de clase por pares no validados",
                today, len(products), counts, dropped)
    return {"products": len(products), **counts, "peer_rejected_downgrades": dropped}

