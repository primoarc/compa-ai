"""Filtro de relevancia para comparar productos comparables.

Problema: cada tienda devuelve resultados "sueltos" para un query. Si tomamos
el más barato sin filtrar, para "televisor 55" gana un adaptador USB o un
soporte de pared en vez de un televisor. Aquí decidimos qué productos
realmente coinciden con la intención de la búsqueda.

Estrategia:
  1. Normalizar (sin acentos, minúsculas) y tokenizar query y nombre.
  2. Sinónimos por tienda: "televisor" == tv == tele == pantalla, etc.
  3. Exigir que TODOS los tokens del query aparezcan en el nombre
     (los números deben calzar exactos: "55" no calza con "5" ni "550").
  4. Excluir accesorios (soporte, rack, cable, control…) salvo que el query
     pida explícitamente un accesorio.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# Grupos de sinónimos: cualquier palabra del grupo satisface a las demás.
_SYN_GROUPS: list[set[str]] = [
    {"televisor", "televisores", "tv", "tele", "pantalla", "television"},
    {"refrigeradora", "refrigerador", "refri", "nevera"},
    {"licuadora", "blender"},
    {"laptop", "portatil", "notebook", "computadora", "compu"},
    {"celular", "telefono", "smartphone", "movil"},
    {"audifonos", "auriculares", "earbuds", "headphones"},
    {"lavadora", "washer"},
    {"microondas", "microwave"},
    {"congelador", "freezer"},
    {"aspiradora", "vacuum"},
    {"pelo", "cabello", "cabellos", "hair"},
    {"secadora", "secadoras", "secador", "secadores", "secado", "dryer"},
]

# Términos de accesorio: si el query NO los pide, se excluyen del resultado
# (un "Soporte para Televisor" no es un televisor).
_ACCESSORY = {
    "soporte", "rack", "base", "mueble", "control", "remoto", "adaptador",
    "cable", "protector", "funda", "montaje", "pedestal", "bracket", "mount",
    "antena", "repuesto", "filtro", "cargador", "forro", "case", "cover",
    "kit", "convertidor", "extension", "regulador", "estuche", "mica",
    "tira", "luces", "iluminacion", "limpiador", "limpieza", "correa",
    "vaso", "jarra", "removedor", "cuchilla", "bolsa", "tapa", "soportes",
}

# Unidades que, pegadas a un número, indican que NO es una talla/medida del
# producto (p.ej. "50 ml" no satisface la búsqueda "pantalla 50").
_UNIT_AFTER = r"(?!\s?(?:ml|g|gr|kg|mg|mah|w|watts?|v|hz|cc)\b)"


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize(s: str) -> str:
    return _strip_accents((s or "").lower())


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokens(s: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(s))


def _synonyms(token: str) -> set[str]:
    for group in _SYN_GROUPS:
        if token in group:
            return group
    return {token}


def _is_number(tok: str) -> bool:
    return tok.isdigit()


# Palabras de relleno que no aportan a la relevancia (no obligan a calzar).
_STOPWORDS = {"de", "para", "con", "el", "la", "los", "las", "y", "o", "un", "una",
              "pulgadas", "plg", "inch", "pulg"}


def is_relevant(query: str, name: str) -> bool:
    """¿El producto `name` coincide con la intención de `query`?"""
    qtoks = [t for t in tokens(query) if t not in _STOPWORDS]
    if not qtoks:
        return True
    name_norm = normalize(name)
    name_toks = set(tokens(name))

    query_wants_accessory = any(t in _ACCESSORY for t in qtoks)
    if not query_wants_accessory:
        if _ACCESSORY & name_toks:
            return False
        # Regla general: "<algo> para <producto>" es un accesorio PARA el
        # producto, no el producto (Motor para Licuadora, Soporte para
        # Televisor, Organizador para Refrigeradora…).
        for t in qtoks:
            if _is_number(t):
                continue
            for syn in _synonyms(t):
                if re.search(rf"\bpara\s+(?:el\s+|la\s+|tu\s+)?{re.escape(syn)}", name_norm):
                    return False

    for t in qtoks:
        if _is_number(t):
            # número exacto, sin dígitos pegados ("55" no calza "5"/"550") y
            # que no sea una unidad como "50 ml" / "1200 w".
            if not re.search(rf"(?<!\d){re.escape(t)}(?!\d){_UNIT_AFTER}", name_norm):
                return False
        else:
            group = _synonyms(t)
            if name_toks & group:
                continue
            # marcas/palabras parciales: permitir como subcadena (samsung…)
            if any(g in name_norm for g in group):
                continue
            return False
    return True


def relevant_products(query: str, products: Iterable) -> list:
    """Productos con precio > 0 que coinciden con el query."""
    return [
        p for p in products
        if getattr(p, "price", None) and p.price > 0 and is_relevant(query, p.name)
    ]


def best_match(query: str, products: Iterable):
    """El producto relevante más barato, o None si ninguno coincide."""
    rel = relevant_products(query, products)
    if not rel:
        return None
    return min(rel, key=lambda p: p.price)
