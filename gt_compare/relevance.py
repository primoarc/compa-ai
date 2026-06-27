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
    {"cafetera", "cafeteras", "percoladora", "percoladoras"},
    {"freidora", "freidoras", "airfryer"},
    {"playera", "playeras", "camiseta", "camisetas", "tshirt"},
    {"tenis", "sneaker", "sneakers", "zapatilla", "zapatillas"},
    {"mochila", "mochilas", "backpack", "backpacks"},
    {"lonchera", "loncheras", "lunchbox", "lunchboxes"},
    {"pachon", "pachones", "termo", "termos"},
    {"panal", "panales", "diaper", "diapers"},
    {"negro", "negra", "black"},
    {"blanco", "blanca", "white"},
    {"rojo", "roja", "red"},
    {"rosado", "rosada", "rosa", "pink"},
    {"azul", "blue"},
    {"celeste", "aqua"},
    {"verde", "green"},
    {"gris", "gray", "grey"},
    {"amarillo", "amarilla", "yellow"},
    {"dorado", "dorada", "gold"},
    {"plateado", "plateada", "silver"},
    {"morado", "morada", "purple"},
    {"cafe", "marron", "brown"},
    {"naranja", "orange"},
    {"pelo", "cabello", "cabellos", "hair"},
    {"secadora", "secadoras", "secador", "secadores", "secado", "dryer"},
    {"alisadora", "alisadoras", "alisador", "alisadores", "straightener"},
    {"ps5", "playstation5"},
    {"playstation", "play", "ps"},
    {"perro", "perros", "canino", "caninos", "canina", "caninas"},
    {
        "treat", "treats", "premio", "premios", "snack", "snacks",
        "golosina", "golosinas", "bocadillo", "bocadillos",
        "galleta", "galletas",
    },
]

# Equivalencias de intención para frases completas. Se usan tanto para filtrar
# resultados como para probar queries alternos en las tiendas.
_ALIAS_GROUPS: list[tuple[str, ...]] = [
    (
        "secadora de pelo",
        "secadora de cabello",
        "secador de pelo",
        "secador de cabello",
        "secador para cabello",
        "hair dryer",
    ),
    (
        "plancha de pelo",
        "plancha de cabello",
        "plancha para cabello",
        "plancha alisadora",
        "alisadora de cabello",
        "alisador de cabello",
        "alisadora",
        "alisador",
        "hair straightener",
    ),
    (
        "ps5",
        "playstation 5",
        "play station 5",
        "playstation5",
        "play 5",
        "consola ps5",
        "consola playstation 5",
        "sony ps5",
    ),
    (
        "treats para perro",
        "treats de perro",
        "premios para perro",
        "premios para perros",
        "snacks para perro",
        "snacks para perros",
        "golosinas para perro",
        "golosinas para perros",
        "galletas para perro",
        "galletas para perros",
        "bocadillos para perro",
        "bocadillos para perros",
    ),
    (
        "playera",
        "camiseta",
        "t-shirt",
        "t shirt",
        "tshirt",
    ),
    (
        "cafetera",
        "percoladora",
        "coffee maker",
        "coffee machine",
        "maquina de cafe",
    ),
    (
        "freidora de aire",
        "air fryer",
        "airfryer",
    ),
    (
        "tenis",
        "zapatos deportivos",
        "sneakers",
        "zapatillas deportivas",
    ),
    (
        "audifonos",
        "auriculares",
        "earbuds",
        "headphones",
    ),
    (
        "mochila",
        "backpack",
    ),
    (
        "lonchera",
        "lunchbox",
    ),
    (
        "pachon",
        "botella de agua",
        "termo",
        "water bottle",
    ),
    (
        "panales",
        "pañales",
        "diapers",
    ),
    (
        "coche de bebe",
        "coche para bebe",
        "carreola",
        "stroller",
    ),
    (
        "bateria externa",
        "power bank",
        "powerbank",
        "cargador portatil",
    ),
    (
        "funda para celular",
        "case para celular",
        "protector para celular",
        "phone case",
    ),
    (
        "comida para perro",
        "alimento para perro",
        "concentrado para perro",
        "dog food",
    ),
    (
        "comida para gato",
        "alimento para gato",
        "concentrado para gato",
        "cat food",
    ),
]

# Términos de accesorio: si el query NO los pide, se excluyen del resultado
# (un "Soporte para Televisor" no es un televisor).
_ACCESSORY = {
    "soporte", "rack", "base", "mueble", "control", "remoto", "adaptador",
    "cable", "protector", "funda", "montaje", "pedestal", "bracket", "mount",
    "antena", "repuesto", "filtro", "cargador", "forro", "case", "cover",
    "kit", "convertidor", "extension", "regulador", "estuche", "mica",
    "tira", "luces", "iluminacion", "limpiador", "limpieza", "correa",
    "vaso", "jarra", "removedor", "cuchilla", "bolsa", "bolsas", "tapa", "soportes",
    "juego", "juegos", "videojuego", "videojuegos", "game", "games",
    "dualsense", "headset", "audifono", "audifonos", "portal", "remote",
    "player", "visor", "vr", "vr2",
    "disco", "unidad", "lector", "libro", "libros", "receta", "recetas",
    "crema", "shampoo", "acondicionador", "tratamiento", "spray", "gel",
    "gancho", "ganchos", "pinza", "pinzas", "peine", "peines", "cepillo", "cepillos",
    "tornillo", "tornillos", "tornilleria", "arandela", "arandelas", "broca",
    "brocas", "punta", "puntas", "porta",
    "pad", "mousepad", "alfombra", "alfombras",
    "botella", "botellas", "burbujas", "minnie", "mickey",
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


def _content_tokens(s: str) -> list[str]:
    return [t for t in tokens(s) if t not in _STOPWORDS]


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


def _alias_tokens(group: tuple[str, ...]) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for alias in group:
        item = tuple(_content_tokens(alias))
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


_ALIAS_TOKEN_GROUPS: list[list[tuple[str, ...]]] = [
    _alias_tokens(group) for group in _ALIAS_GROUPS
]


def _alias_token_set(anchor: str) -> set[tuple[str, ...]]:
    anchor_tokens = tuple(_content_tokens(anchor))
    for group in _ALIAS_TOKEN_GROUPS:
        if anchor_tokens in group:
            return set(group)
    return set()


_CONSOLE_ALIAS_TOKENS = _alias_token_set("ps5")
_HAIR_DRYER_ALIAS_TOKENS = _alias_token_set("secadora de pelo")
_HAIR_STRAIGHTENER_ALIAS_TOKENS = _alias_token_set("plancha de pelo")
_PET_TREAT_ALIAS_TOKENS = _alias_token_set("treats para perro")
_PET_FOOD_ALIAS_TOKENS = (
    _alias_token_set("comida para perro") | _alias_token_set("comida para gato")
)
_BRAND_QUERY_TOKENS = {"owala"}
_OWALA_PRODUCT_TOKENS = {
    "botella", "botellas", "pachon", "pachones", "termo", "termos",
    "vaso", "vasos", "tumbler", "tumblers", "mug", "mugs",
}
_OWALA_ACCESSORY_TOKENS = {
    "funda", "fundas", "protector", "protectora", "protectores",
    "boot", "boots", "accesorio", "accesorios", "limpiador",
    "limpiadores", "cepillo", "cepillos", "repuesto", "repuestos",
}

_AIR_CONDITIONER_POSITIVE = re.compile(
    r"\baire(?:s)?\s+acondicionado(?:s)?\b|\bair\s+conditioner\b|\bac\s+portatil\b|\bportable\s+ac\b",
    re.I,
)


def _replace_once(
    seq: tuple[str, ...],
    old: tuple[str, ...],
    new: tuple[str, ...],
) -> tuple[str, ...] | None:
    if not old:
        return None
    width = len(old)
    for idx in range(0, len(seq) - width + 1):
        if seq[idx:idx + width] == old:
            return seq[:idx] + new + seq[idx + width:]
    return None


def query_token_variants(query: str, *, limit: int = 16) -> list[tuple[str, ...]]:
    """Devuelve variantes tokenizadas del query usando aliases controlados."""
    base = tuple(_content_tokens(query))
    if not base:
        return [()]

    variants: list[tuple[str, ...]] = [base]
    seen = {base}
    for group in _ALIAS_TOKEN_GROUPS:
        for current in list(variants):
            for alias in group:
                replaced = _replace_once(current, alias, alias)
                if replaced is None:
                    continue
                for alternative in group:
                    candidate = _replace_once(current, alias, alternative)
                    if candidate and candidate not in seen:
                        seen.add(candidate)
                        variants.append(candidate)
                        if len(variants) >= limit:
                            return variants
    return variants


def search_queries(query: str, *, limit: int = 6) -> list[str]:
    """Queries alternos para buscadores que no entienden abreviaturas."""
    clean = " ".join(query.strip().split())
    if not clean:
        return []

    out = [clean]
    seen = {normalize(clean)}
    base = tuple(_content_tokens(query))

    for aliases, token_group in zip(_ALIAS_GROUPS, _ALIAS_TOKEN_GROUPS):
        if base in token_group:
            for alias in aliases:
                if normalize(alias) not in seen:
                    seen.add(normalize(alias))
                    out.append(alias)
                    if len(out) >= limit:
                        return out
            return out

    for variant in query_token_variants(query):
        text = " ".join(variant)
        if text and normalize(text) not in seen:
            seen.add(normalize(text))
            out.append(text)
            if len(out) >= limit:
                break
    return out


def _is_console_query(query: str) -> bool:
    base = tuple(_content_tokens(query))
    return base in _CONSOLE_ALIAS_TOKENS


def _is_air_conditioner_query(query: str) -> bool:
    qtoks = set(_content_tokens(query))
    return (
        {"aire", "acondicionado"} <= qtoks
        or {"air", "conditioner"} <= qtoks
        or ("ac" in qtoks and bool(qtoks & {"portatil", "portable"}))
    )


def _is_brand_query(query: str) -> bool:
    qtoks = _content_tokens(query)
    return len(qtoks) == 1 and qtoks[0] in _BRAND_QUERY_TOKENS


def _is_owala_query(query: str) -> bool:
    qtoks = _content_tokens(query)
    return len(qtoks) == 1 and qtoks[0] == "owala"


def _owala_query_matches(name_toks: set[str]) -> bool:
    return (
        "owala" in name_toks
        and bool(name_toks & _OWALA_PRODUCT_TOKENS)
        and not bool(name_toks & _OWALA_ACCESSORY_TOKENS)
    )


def _air_conditioner_query_matches(query_toks: list[str], name_norm: str, name_toks: set[str]) -> bool:
    if not _AIR_CONDITIONER_POSITIVE.search(name_norm):
        return False
    if "portatil" in query_toks or "portable" in query_toks:
        return bool(name_toks & {"portatil", "portable"})
    return True


def _allows_for_phrase(query: str) -> bool:
    base = tuple(_content_tokens(query))
    return (
        base in _HAIR_DRYER_ALIAS_TOKENS
        or base in _HAIR_STRAIGHTENER_ALIAS_TOKENS
        or base in _PET_TREAT_ALIAS_TOKENS
        or base in _PET_FOOD_ALIAS_TOKENS
    )


def _plan_attr(plan, name: str, default):
    if plan is None:
        return default
    if isinstance(plan, dict):
        return plan.get(name, default)
    return getattr(plan, name, default)


def _term_matches(term: str, name_norm: str, name_toks: set[str]) -> bool:
    term_norm = normalize(term)
    term_toks = tokens(term_norm)
    if not term_toks:
        return False
    if len(term_toks) > 1 and term_norm in name_norm:
        return True
    return all(_variant_matches((tok,), name_norm, name_toks) for tok in term_toks)


def _plan_excludes_match(plan, name_norm: str, name_toks: set[str], query_toks: list[str]) -> bool:
    for term in _plan_attr(plan, "exclude_terms", []) or []:
        term_toks = tokens(term)
        if not term_toks:
            continue
        # Si el usuario pidió explícitamente ese término, no lo tratamos como
        # exclusión. Ej: "crema alisadora" sí debe permitir crema.
        if any(tok in query_toks for tok in term_toks):
            continue
        if _term_matches(term, name_norm, name_toks):
            return True
    return False


def _plan_required_matches(query: str, plan, name_norm: str, name_toks: set[str]) -> bool:
    groups = _plan_attr(plan, "required_any_groups", []) or []
    if not groups:
        return False
    # OpenAI puede generar un grupo demasiado amplio ("cabello") para una
    # búsqueda con varios conceptos ("plancha de pelo"). En esos casos no
    # dejamos que el plan amplíe la relevancia salvo que haya al menos dos
    # grupos independientes que el título deba satisfacer.
    if len(_content_tokens(query)) >= 2 and len(groups) < 2:
        return False
    for group in groups:
        if not any(_term_matches(term, name_norm, name_toks) for term in group):
            return False
    return True


def is_relevant(query: str, name: str, plan=None) -> bool:
    """¿El producto `name` coincide con la intención de `query`?"""
    qvariants = query_token_variants(query)
    if not qvariants or not qvariants[0]:
        return True
    name_norm = normalize(name)
    name_toks = set(tokens(name))

    original_qtoks = _content_tokens(query)
    if _plan_excludes_match(plan, name_norm, name_toks, original_qtoks):
        return False
    if _is_air_conditioner_query(query) and not _air_conditioner_query_matches(original_qtoks, name_norm, name_toks):
        return False
    if _is_owala_query(query):
        return _owala_query_matches(name_toks)

    query_wants_accessory = any(t in _ACCESSORY for t in original_qtoks) or _is_brand_query(query)
    allows_for_phrase = _allows_for_phrase(query)
    if not query_wants_accessory:
        if _ACCESSORY & name_toks:
            return False
        # Regla general: "<algo> para <producto>" es un accesorio PARA el
        # producto, no el producto (Motor para Licuadora, Soporte para
        # Televisor, Organizador para Refrigeradora…).
        for variant in qvariants:
            for t in variant:
                if _is_number(t):
                    continue
                for syn in _synonyms(t):
                    accessory_for_query = (
                        rf"\bpara\s+(?:el\s+|la\s+|tu\s+)?{re.escape(syn)}"
                        rf"|\bpara\s+uso\s+con\s+(?:el\s+|la\s+|tu\s+)?{re.escape(syn)}"
                    )
                    if not allows_for_phrase and re.search(accessory_for_query, name_norm):
                        return False

    if _is_console_query(query) and not query_wants_accessory:
        if not ({"consola", "console"} & name_toks):
            return False

    if any(
        _variant_matches(variant, name_norm, name_toks)
        for variant in qvariants
    ):
        return True

    return _plan_required_matches(query, plan, name_norm, name_toks)


def _variant_matches(qtoks: tuple[str, ...], name_norm: str, name_toks: set[str]) -> bool:
    for t in qtoks:
        if _is_number(t):
            # número exacto, sin dígitos pegados ("55" no calza "5"/"550") y
            # que no sea una unidad como "50 ml" / "1200 w".
            if not re.search(rf"(?<!\d){re.escape(t)}(?!\d){_UNIT_AFTER}", name_norm):
                return False
            continue

        group = _synonyms(t)
        if name_toks & group:
            continue
        # marcas/palabras parciales: permitir como subcadena (samsung…)
        if any(len(g) >= 5 and g in name_norm for g in group):
            continue
        return False
    return True


def relevant_products(query: str, products: Iterable, plan=None) -> list:
    """Productos con precio > 0 que coinciden con el query."""
    return [
        p for p in products
        if getattr(p, "price", None) and p.price > 0 and is_relevant(query, p.name, plan=plan)
    ]


def best_match(query: str, products: Iterable, plan=None):
    """El producto relevante más barato, o None si ninguno coincide."""
    rel = relevant_products(query, products, plan=plan)
    if not rel:
        return None
    return min(rel, key=lambda p: p.price)
