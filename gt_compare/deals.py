"""Detector de precios anómalos ("price errors") para uso personal.

De vez en cuando una tienda publica un precio equivocado —un QLED de 65" a
Q2,900— y lo respeta si uno ordena antes de que lo corrijan. Este módulo busca
esos casos en los datos que el comparador ya trae, sin infraestructura nueva.

Un error de precio es un valor atípico, y aquí se detecta por dos caminos:

  1. Cruce entre tiendas (señal fuerte). Si `matching` confirma que es el mismo
     modelo en varias tiendas y una lo tiene muy por debajo de la mediana de
     las demás, la comparación es entre productos idénticos y el desvío es
     difícil de explicar por diferencia de especificaciones.

  2. Cohorte por tamaño (señal media). Dentro de una misma diagonal —todos los
     de 65"— un precio muy por debajo de la mediana es sospechoso aunque no
     tengamos el mismo modelo en otra tienda. Tiene más ruido, porque un LED
     básico y un OLED de 65" no valen lo mismo, así que exige un desvío mayor.

Deliberadamente NO se marcan productos agotados: un error de precio que no se
puede comprar no sirve de nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from . import matching

# Con tres o más tiendas la mediana es sólida y basta un desvío moderado.
CROSS_STORE_MIN_DISCOUNT = 0.35
# Con solo dos, el "precio normal" es un único dato: se exige mucho más.
TWO_STORE_MIN_DISCOUNT = 0.50
# La cohorte mezcla gamas distintas, así que el umbral es alto.
COHORT_MIN_DISCOUNT = 0.55
COHORT_MIN_ITEMS = 5
# Contra el precio de lista de la propia tienda. Una rebaja de 30-50% es
# rutina en el retail guatemalteco; de 70% para arriba ya no es promoción.
LIST_PRICE_MIN_DISCOUNT = 0.70
LIST_PRICE_HIGH_DISCOUNT = 0.80
# Piso global: debajo de esto no hay nada que valga la pena revisar.
MIN_PRICE = 150.0
# Al comparar entre tiendas o por cohorte, un precio bajo suele ser un accesorio
# mal clasificado, así que ahí el piso es más alto.
CROSS_MIN_PRICE = 400.0
# Contra el precio de lista el piso va sobre la REFERENCIA, no sobre el precio
# de venta: una laptop a Q299 con lista de Q2,899 es justo el caso interesante,
# y un cable a Q50 con lista de Q100 no lo es.
LIST_MIN_REFERENCE = 800.0


@dataclass
class Anomaly:
    kind: str                # "modelo" | "cohorte"
    confidence: str          # "alta" | "media"
    query: str
    store: str
    store_key: str
    name: str
    price: float
    reference: float         # precio "normal" con el que se compara
    url: str
    peers: int               # cuántos puntos de comparación respaldan la señal
    size: Optional[float] = None
    model: str = ""
    others: list = field(default_factory=list)  # (tienda, precio) de referencia

    @property
    def discount(self) -> float:
        if not self.reference:
            return 0.0
        return 1.0 - (self.price / self.reference)

    @property
    def savings(self) -> float:
        return self.reference - self.price


def _offers_from_rows(rows: list) -> list:
    """Aplana todas las ofertas con precio y disponibilidad de una búsqueda."""
    out = []
    for row in rows:
        if not row.get("ok"):
            continue
        for item in row.get("items") or []:
            if not item.get("price") or item["price"] < MIN_PRICE:
                continue
            if not item.get("available"):
                continue  # un error de precio agotado no sirve
            out.append({
                **item,
                "store": row.get("store"),
                "store_key": row.get("store_key"),
            })

    return out


def _cross_store(query: str, offers: list) -> list:
    """Mismo modelo confirmado, una tienda muy por debajo de las demás."""
    found = []
    offers = [o for o in offers if o["price"] >= CROSS_MIN_PRICE]
    for group in matching.group_offers(offers, min_stores=2):
        cheap, *rest = group["offers"]
        if not rest:
            continue
        ref = median(o["price"] for o in rest)
        if ref <= 0:
            continue
        discount = 1.0 - (cheap["price"] / ref)
        threshold = (
            TWO_STORE_MIN_DISCOUNT if len(group["offers"]) == 2
            else CROSS_STORE_MIN_DISCOUNT
        )
        if discount < threshold:
            continue
        found.append(Anomaly(
            kind="modelo",
            confidence="alta" if len(group["offers"]) >= 3 else "media",
            query=query,
            store=cheap["store"],
            store_key=cheap["store_key"],
            name=cheap["name"],
            price=cheap["price"],
            reference=ref,
            url=cheap.get("url", ""),
            peers=len(rest),
            size=group.get("size"),
            model=group.get("model", ""),
            others=[(o["store"], o["price"]) for o in rest],
        ))
    return found


def _vs_list_price(query: str, offers: list) -> list:
    """Muy por debajo del precio de lista que declara la misma tienda.

    Es la referencia más autorizada que hay: no depende de encontrar el mismo
    modelo en otra tienda ni de tener historial. Fue lo que delató una Lenovo
    de 14" a Q529 cuyo propio precio de lista en Siman era Q3,499.
    """
    found = []
    for o in offers:
        lista = o.get("list_price")
        if not lista or lista <= o["price"] or lista < LIST_MIN_REFERENCE:
            continue
        discount = 1.0 - (o["price"] / lista)
        if discount < LIST_PRICE_MIN_DISCOUNT:
            continue
        found.append(Anomaly(
            kind="lista",
            confidence="alta" if discount >= LIST_PRICE_HIGH_DISCOUNT else "media",
            query=query,
            store=o["store"],
            store_key=o["store_key"],
            name=o["name"],
            price=o["price"],
            reference=lista,
            url=o.get("url", ""),
            peers=0,  # la referencia es la tienda misma, no otras tiendas
            size=matching.screen_size(o.get("name", "")),
        ))
    return found


def _cohort(query: str, offers: list) -> list:
    """Muy por debajo de la mediana de su misma diagonal."""
    by_size: dict = {}
    for o in offers:
        size = matching.screen_size(o.get("name", ""))
        if size is None:
            continue  # sin cohorte definible, no se evalúa
        by_size.setdefault(size, []).append(o)

    found = []
    for size, group in by_size.items():
        group = [o for o in group if o["price"] >= CROSS_MIN_PRICE]
        if len(group) < COHORT_MIN_ITEMS:
            continue
        prices = sorted(o["price"] for o in group)
        ref = median(prices)
        for o in group:
            discount = 1.0 - (o["price"] / ref) if ref else 0.0
            if discount < COHORT_MIN_DISCOUNT:
                continue
            found.append(Anomaly(
                kind="cohorte",
                confidence="media",
                query=query,
                store=o["store"],
                store_key=o["store_key"],
                name=o["name"],
                price=o["price"],
                reference=ref,
                url=o.get("url", ""),
                peers=len(group) - 1,
                size=size,
            ))
    return found


def find_anomalies(query: str, rows: list) -> list:
    """Precios sospechosamente bajos en los resultados de una búsqueda."""
    offers = _offers_from_rows(rows)
    if not offers:
        return []

    found = (
        _cross_store(query, offers)
        + _vs_list_price(query, offers)
        + _cohort(query, offers)
    )

    # Un mismo producto puede caer por ambos caminos: se queda la señal más
    # fuerte (el cruce entre tiendas compara productos idénticos).
    rank = {"modelo": 0, "lista": 1, "cohorte": 2}
    best: dict = {}
    for a in found:
        key = (a.store_key, a.name, a.price)
        prev = best.get(key)
        if prev is None or rank[a.kind] < rank[prev.kind]:
            best[key] = a
    return sorted(best.values(), key=lambda a: a.discount, reverse=True)


def post_text(a: Anomaly) -> str:
    """Borrador de publicación para redes. No publica nada por sí solo."""
    pct = round(a.discount * 100)
    ref = f"Q{a.reference:,.0f}"
    price = f"Q{a.price:,.0f}"
    if a.kind == "modelo":
        contexto = f"El mismo modelo está a {ref} en {a.peers} tienda(s) más."
    elif a.kind == "lista":
        contexto = f"La tienda misma lo lista en {ref}."
    else:
        size = f'{a.size:g}"' if a.size else "su categoría"
        contexto = f"La mediana de {size} anda en {ref}."
    return (
        f"⚡ {a.name[:90]}\n"
        f"{a.store}: {price}  (-{pct}%)\n"
        f"{contexto}\n"
        f"{a.url}"
    )
