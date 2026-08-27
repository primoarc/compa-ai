"""Comparación del MISMO producto entre catálogos completos de varias tiendas.

El precio de lista es una afirmación de la tienda, y cada una la usa distinto:
Siman lo pone como precio real, Cemaco corre campañas permanentes contra el
suyo. Compararse contra uno mismo es circular.

La referencia honesta es el mercado: lo que ese mismo producto cuesta en otra
tienda. `matching` ya sabe decidir si dos títulos son el mismo producto; acá se
aplica sobre los catálogos enteros en vez de sobre 24 resultados de búsqueda.

Se indexa por código de modelo en vez de comparar todos contra todos: 32,000
por 25,000 son 800 millones de pares, y con índice son dos pasadas lineales.
"""

from __future__ import annotations

import collections
from statistics import median

from . import matching

# Debajo de esto un "descuento" enorme suele ser un accesorio mal emparejado.
MIN_PRECIO = 300.0
# Cuánto por debajo de las otras tiendas para que valga la pena mirarlo.
MIN_BRECHA = 0.35


def _indexar(catalogos: dict) -> dict:
    """código de modelo -> [(tienda, producto)], solo códigos en 2+ tiendas."""
    idx: dict = collections.defaultdict(list)
    for tienda, productos in catalogos.items():
        for p in productos:
            precio = p.get("price")
            if not precio or precio < MIN_PRECIO:
                continue
            for code in matching.model_codes(p.get("name", "")):
                idx[code].append((tienda, p))
    return {
        code: filas
        for code, filas in idx.items()
        if len({t for t, _ in filas}) >= 2
    }


def comparar(catalogos: dict, *, min_brecha: float = MIN_BRECHA) -> list:
    """Productos que una tienda tiene muy por debajo del resto del mercado."""
    hallazgos = []
    vistos: set = set()

    for code, filas in _indexar(catalogos).items():
        # Una oferta por tienda: la más barata.
        por_tienda: dict = {}
        for tienda, p in filas:
            if tienda not in por_tienda or p["price"] < por_tienda[tienda]["price"]:
                por_tienda[tienda] = p
        if len(por_tienda) < 2:
            continue

        ofertas = sorted(por_tienda.items(), key=lambda kv: kv[1]["price"])
        (t_bajo, p_bajo), *resto = ofertas

        # Las guardas de matching valen igual acá: mismo código no basta si las
        # marcas, tamaños o capacidades declaradas se contradicen.
        nombre_bajo = p_bajo.get("name", "")
        comparables = []
        for t, p in resto:
            if matching._same_product(
                {
                    "_codes": matching.model_codes(nombre_bajo),
                    "_size": matching.screen_size(nombre_bajo),
                    "_brands": matching.brands(nombre_bajo),
                    "_cpu": matching.cpu_model(nombre_bajo),
                    "_caps": matching.capacities(nombre_bajo),
                },
                {
                    "_codes": matching.model_codes(p.get("name", "")),
                    "_size": matching.screen_size(p.get("name", "")),
                    "_brands": matching.brands(p.get("name", "")),
                    "_cpu": matching.cpu_model(p.get("name", "")),
                    "_caps": matching.capacities(p.get("name", "")),
                },
            ):
                comparables.append((t, p))
        if not comparables:
            continue

        referencia = median(p["price"] for _, p in comparables)
        if referencia <= 0:
            continue
        brecha = 1.0 - (p_bajo["price"] / referencia)
        if brecha < min_brecha:
            continue

        clave = (t_bajo, p_bajo.get("url"))
        if clave in vistos:
            continue
        vistos.add(clave)

        hallazgos.append({
            "modelo": code,
            "brecha": brecha,
            "tienda": t_bajo,
            "producto": p_bajo,
            "otras": [(t, p["price"]) for t, p in comparables],
            "referencia": referencia,
        })

    hallazgos.sort(key=lambda h: -h["brecha"])
    return hallazgos
