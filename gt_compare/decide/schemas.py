"""Las cinco decisiones del sistema: preguntas para Jev y su fallback determinístico.

Regla de costos: lo que se puede decidir con código (EAN igual, aritmética de
precios, regex de tamaños) se decide antes y nunca llega a Jev. Las preguntas
van en inglés porque Jev rinde mejor en inglés; los datos van tal cual.
"""

from __future__ import annotations

import re
from typing import Optional

from .. import matching
from ..relevance import normalize
from . import taxonomy
from .core import Decision, Question, Schema

# --- 1. matching ------------------------------------------------------------
MATCH_LEVELS = [
    "They are two different products.",
    "They are closely related but may not be the same sellable item: a different "
    "color, size, capacity, storage, bundle or model year of the same line, or names "
    "too vague to tell.",
    "They are one and the same product (same brand, same model, same specs).",
]

MATCH = Schema(
    name="match",
    version="1",
    primary="relation",
    questions={
        "relation": Question(
            "score",
            "How do `product_a` and `product_b` relate? They come from different stores in "
            "Guatemala, so wording, word order and abbreviations differ. Ignore price.",
            MATCH_LEVELS,
        ),
        "same_brand": Question("noul", "Do `product_a` and `product_b` have the same brand?"),
        "same_model": Question(
            "noul", "Do `product_a` and `product_b` name the same model number or model line?"
        ),
        "same_specs": Question(
            "noul",
            "Do `product_a` and `product_b` have the same size, capacity, color and "
            "configuration, as far as the names say?",
        ),
    },
)

# Umbrales sobre P(mismo producto) = probabilidad del nivel 2.
MATCH_ACCEPT = 0.85
MATCH_REVIEW = 0.50


def match_state(a: dict, b: dict) -> dict:
    """Solo lo que describe al producto. El precio no entra: si entrara, una
    diferencia grande (justo un price error) empujaría a "distintos"."""
    def side(p: dict) -> dict:
        out = {"name": p.get("name") or ""}
        for k in ("brand", "category"):
            if p.get(k):
                out[k] = p[k]
        return out

    return {"product_a": side(a), "product_b": side(b)}


def _prep(p: dict) -> dict:
    name = p.get("name") or ""
    return {
        "_codes": matching.model_codes(name),
        "_brands": matching.brands(name) | ({(p.get("brand") or "").lower()} - {""}),
        "_size": matching.screen_size(name),
        "_cpu": matching.cpu_model(name),
        "_caps": matching.capacities(name),
    }


def match_rules(state: dict) -> Decision:
    """La regla actual del comparador: código de modelo compartido sin specs que choquen."""
    a, b = _prep(state["product_a"]), _prep(state["product_b"])
    shared = matching._same_product(a, b)
    if shared:
        return Decision(2, None, source="rules", answers={"shared_code": shared})
    if a["_codes"] & b["_codes"]:
        return Decision(1, None, source="rules")  # mismo código, specs distintas: variante
    return Decision(0, None, source="rules")


def ean_pair_rules(state: dict) -> Decision:
    """Validación de un par que ya comparte EAN: se acepta salvo contradicción clara.

    Es el fallback cuando Jev está apagado. Solo mira lo que dice el nombre
    (marca, pulgadas, procesador, código de modelo): la marca que declara cada
    tienda es demasiado sucia ("MNY" contra "Maybelline"). En los 123 pares
    auditados con el mismo EAN atrapa 1 de los 10 malos y rechaza 1 de los 113
    buenos: la validación útil es la de Jev.
    """
    a, b = state["product_a"]["name"], state["product_b"]["name"]
    conflicts = []
    ba, bb = matching.brands(a), matching.brands(b)
    if ba and bb and not (ba & bb):
        conflicts.append("marca")
    sa, sb = matching.screen_size(a), matching.screen_size(b)
    if sa and sb and sa != sb:
        conflicts.append("tamaño")
    ca, cb = matching.cpu_model(a), matching.cpu_model(b)
    if ca and cb and ca != cb:
        conflicts.append("procesador")
    xa, xb = matching.model_codes(a), matching.model_codes(b)
    if xa and xb and not matching._codes_overlap(xa, xb):
        conflicts.append("modelo")
    if matching.bundle_mismatch(a, b):
        conflicts.append("paquete")
    return Decision(0 if conflicts else 2, None, source="rules", answers={"conflicts": conflicts})


def p_same(dec: Decision) -> Optional[float]:
    if dec.source == "rules":
        return 1.0 if dec.value == 2 else 0.0
    probs = (dec.answers.get("relation") or {}).get("probabilities") or {}
    return float(probs.get("2", 0.0))


def match_outcome(dec: Decision) -> str:
    """accept | review | discard."""
    p = p_same(dec)
    if p is None:
        return "discard"
    if p >= MATCH_ACCEPT:
        return "accept"
    if p >= MATCH_REVIEW:
        return "review"
    return "discard"


# --- 2. categorización ------------------------------------------------------
CATEGORY_L1 = Schema(
    name="category_l1",
    version="1",
    primary="category",
    questions={
        "category": Question(
            "choice",
            "Which department of a Guatemalan retail catalog does this product belong to? "
            "Accessories go with what they are for only if they are sold as accessories "
            "(a TV mount is a TV accessory, not a TV).",
            dict(taxonomy.LEVEL1_DESCRIPTIONS),
        )
    },
)


def category_l2_schema(level1: str) -> Optional[Schema]:
    options = taxonomy.level2_options(level1)
    if len(options) < 2:
        return None
    return Schema(
        name=f"category_l2:{level1}",
        version="1",
        primary="subcategory",
        questions={
            "subcategory": Question(
                "choice",
                "Which subcategory fits this product best?",
                {o: ", ".join(taxonomy.TAXONOMY[level1][o][:6]) for o in options},
            )
        },
    )


CATEGORY_ACCEPT = 0.7


def category_state(name: str, brand: Optional[str] = None, store_category: Optional[str] = None) -> dict:
    out = {"product": name}
    if brand:
        out["brand"] = brand
    if store_category:
        out["store_category"] = store_category
    return out


def category_l1_rules(state: dict) -> Decision:
    l1, _, weight = taxonomy.classify_rules(state["product"], state.get("store_category"))
    return Decision(l1, None, source="rules", answers={"weight": weight})


def category_l2_rules(state: dict) -> Decision:
    _, l2, _ = taxonomy.classify_rules(state["product"], state.get("store_category"))
    return Decision(l2, None, source="rules")


# --- 3. causa de un posible price error ------------------------------------
CAUSES = {
    "error_real": "A genuine pricing mistake by the store: the same product and unit, "
                  "sold far below its normal price with no stated condition.",
    "liquidacion": "A deliberate clearance or promotion the store announces (outlet, "
                   "last units, liquidation, event sale).",
    "cambio_variante": "The listing now points to a cheaper variant, size, bundle or "
                       "refurbished/open-box unit, so the prices are not comparable.",
    "moneda_unidad": "Currency or unit confusion: a price in US dollars instead of "
                     "quetzales, a per-unit price for a multipack, or a monthly installment "
                     "shown as the price.",
    "condicion_tarjeta": "The low price only applies with a payment condition: a specific "
                         "bank card, cash-only payment, membership, coupon or financing plan.",
    "dato_corrupto": "Broken data: placeholder price, missing digits, wrong product "
                     "attached to the listing.",
}

PRICE_CAUSE = Schema(
    name="price_cause",
    version="2",
    primary="cause",
    questions={
        "cause": Question(
            "choice",
            "A price monitor flagged this listing because its price dropped far below "
            "`reference`. The percentages were computed in code and are correct. "
            "What is the most likely cause?",
            CAUSES,
        )
    },
)

CAUSE_ACCEPT = 0.8
IMPLAUSIBLE_RATIO = 25  # 96% de descuento o más: casi siempre un dato roto, no un error de precio
USD_RATE = 7.7

_CARD = re.compile(r"\b(tarjeta|banco|bi |bac|visa|mastercard|credomatic|cuotas?|meses|"
                   r"financiamiento|cupon|membresia|club|efectivo|contado)\b")
_CLEARANCE = re.compile(r"\b(liquidacion|outlet|ultimas? unidades|remate|black friday|"
                        r"cyber|hot sale|exhibicion)\b")
_VARIANT = re.compile(r"\b(reacondicionad[oa]|refurbished|open box|caja abierta|usad[oa]|"
                      r"seminuevo|exhibicion)\b")


def price_cause_state(*, name: str, store: str, price: float, reference: float,
                      reference_kind: str, drop_pct: float, list_price: Optional[float],
                      promo_text: Optional[str], other_stores: list[dict],
                      previous_price: Optional[float]) -> dict:
    return {
        "product": name,
        "store": store,
        "price_gtq": round(price, 2),
        "reference": {"kind": reference_kind, "price_gtq": round(reference, 2),
                      "drop_percent": round(drop_pct * 100)},
        "list_price_gtq": round(list_price, 2) if list_price else None,
        "previous_price_gtq": round(previous_price, 2) if previous_price else None,
        "store_promo_text": promo_text or None,
        "same_product_other_stores": other_stores[:5],
    }


def price_cause_rules(state: dict) -> Decision:
    text = normalize(" ".join(str(x) for x in (state.get("product"), state.get("store_promo_text")) if x))
    price = float(state["price_gtq"])
    ref = float(state["reference"]["price_gtq"])
    if price < 1 or not state.get("product") or (ref > 0 and ref / price > IMPLAUSIBLE_RATIO):
        return Decision("dato_corrupto", None, source="rules")
    if _CARD.search(f" {text} "):
        return Decision("condicion_tarjeta", None, source="rules")
    if _VARIANT.search(f" {text} "):
        return Decision("cambio_variante", None, source="rules")
    if ref > 0 and abs(price * USD_RATE / ref - 1) < 0.12:
        return Decision("moneda_unidad", None, source="rules")
    if _CLEARANCE.search(f" {text} "):
        return Decision("liquidacion", None, source="rules")
    return Decision("error_real", None, source="rules")


def cause_accepts(dec: Decision) -> bool:
    """¿Entra a la cola de aprobación? Con Jev: 'error_real' con P ≥ 0.8.
    Sin Jev (reglas): 'error_real' entra marcado como no verificado."""
    if dec.value != "error_real":
        return False
    return dec.source == "rules" or (dec.probability or 0.0) >= CAUSE_ACCEPT


# --- 4. intención de búsqueda ----------------------------------------------
INTENT = Schema(
    name="intent",
    version="1",
    primary="category",
    questions={
        "category": Question(
            "choice",
            "A shopper in Guatemala typed `query` into a price comparison site (Spanish, "
            "often slang or abbreviations like 'tele', 'refri', 'compu'). Which department "
            "are they looking for?",
            dict(taxonomy.LEVEL1_DESCRIPTIONS),
        ),
        "wants_cheapest": Question(
            "noul",
            "Does `query` ask for the cheapest or a budget option (e.g. 'barata', "
            "'económico', 'más barato', 'de oferta')?",
        ),
    },
)

INTENT_TIMEOUT = 0.4  # segundos; en tiempo real no esperamos más

_CHEAP = re.compile(r"\b(barat[oa]s?|economic[oa]s?|mas barat[oa]|oferta|ofertas|low cost|"
                    r"precio bajo)\b")
def intent_state(query: str) -> dict:
    return {"query": query}


def intent_rules(state: dict) -> Decision:
    q = state["query"]
    l1, _, weight = taxonomy.classify_rules(q)
    # Formas cortas que el catálogo no usa pero la gente sí.
    short = normalize(q)
    for word, cat in (("tele", "televisores_video"), ("refri", "linea_blanca"),
                      ("compu", "computacion"), ("cel ", "celulares_tablets"),
                      ("audifonos", "audio"), ("micro", "electrodomesticos_pequenos")):
        if weight == 0 and f" {word}" in f" {short} ":
            l1, weight = cat, 1.0
    cheap = bool(_CHEAP.search(short))
    return Decision(l1, None, source="rules",
                    answers={"wants_cheapest": {"type": "noul", "noul": 1.0 if cheap else 0.0}})


def intent_sort(dec: Decision) -> str:
    noul = (dec.answers.get("wants_cheapest") or {}).get("noul")
    return "price_asc" if (noul or 0) >= 0.5 else "relevance"


# --- 5. oferta del día (queda en reglas: no hay verdad de terreno para medir a Jev) ---
def daily_state(deal: dict) -> dict:
    return {
        "product": deal["name"],
        "store": deal["store"],
        "price_gtq": round(deal["price"], 2),
        "usual_price_gtq": round(deal["reference"], 2),
        "savings_percent": round(deal["drop_pct"] * 100),
    }


def daily_rules(state: dict) -> Decision:
    """Sin Jev, el atractivo es solo el ahorro: 0..3 por tramos."""
    pct = state["savings_percent"]
    level = 3 if pct >= 50 else 2 if pct >= 30 else 1 if pct >= 15 else 0
    return Decision(level, None, source="rules")


def expected_level(dec: Decision) -> float:
    probs = (dec.answers.get("appeal") or {}).get("probabilities")
    if dec.source == "rules" or not probs:
        return float(dec.value)
    return sum(int(k) * float(v) for k, v in probs.items())

