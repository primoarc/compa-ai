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
    "deflector", "deflectores", "escudo", "escudos", "cubierta", "cubiertas",
    "manguera", "mangueras", "valvula", "valvulas", "sensor", "sensores",
    "termostato", "termostatos", "instalacion", "mantenimiento", "limpieza",
    "stand", "stands", "empaque", "empaques", "empaquetadura",
    # Tomas, conectores y placas de pared: "TOMA TV COAXIAL" a Q3.52
    # llegó a encabezar la búsqueda de "televisor".
    "toma", "tomas", "coaxial", "conector", "conectores", "enchufe",
    "enchufes", "roseta", "rosetas", "placa", "placas", "jack",
    "splitter", "divisor", "divisores",
}

# Marcadores de juguete. La versión de juguete de un aparato ("Winfun Laptop
# Unicornio", "Laptop Kids Montessori juguete") gana siempre el puesto de "más
# barato" y arruina el resultado principal. Namespaced: solo se aplica cuando la
# query pide un aparato de estas categorías y el usuario no pidió un juguete.
_TOY_MARKERS = {
    "juguete", "juguetes", "toy", "toys", "juguetero", "juguetera",
    "didactico", "didactica", "infantil", "infantiles", "montessori",
    "winfun", "kids", "junior", "preescolar",
    # Miniaturas decorativas: "Villa navideña televisor con luz y música"
    # aparecía como uno de los televisores más baratos.
    "navideno", "navidena", "navidenos", "navidenas", "navidad",
    "adorno", "adornos", "decoracion", "decorativo", "decorativa",
    "miniatura", "miniaturas", "villa",
}
_TOY_SENSITIVE_ANCHORS = (
    "televisor", "laptop", "celular", "licuadora", "refrigeradora",
    "lavadora", "microondas", "aspiradora", "cafetera", "freidora",
)
_TOY_SENSITIVE_TOKENS: set[str] = set().union(
    *(next(g for g in _SYN_GROUPS if anchor in g) for anchor in _TOY_SENSITIVE_ANCHORS)
)

# "portatil" es adjetivo, no sustantivo: está en el grupo de sinónimos de laptop
# porque "Computadora Portátil" sí es una laptop, pero eso hace que "Mesa
# portátil de computadora" o "Estación de energía portátil" también califiquen.
# Si el título nombra otra categoría de producto, no es una laptop.
_LAPTOP_TOKENS = next(g for g in _SYN_GROUPS if "laptop" in g)
# Solo sustantivos de categoría que prácticamente nunca aparecen en el título de
# una laptop real. Deliberadamente NO se incluyen "pantalla", "teclado" ni
# "bateria": son specs legítimas ("teclado retroiluminado", "pantalla 15.6\"").
_LAPTOP_EXCLUDE = {
    "mesa", "mesas", "escritorio", "escritorios", "silla", "sillas",
    "monitor", "monitores", "proyector", "proyectores",
    "estacion", "estaciones", "powerbank", "inversor", "inversores",
    "generador", "generadores", "impresora", "impresoras", "escaner",
    "bocina", "bocinas", "ventilador", "ventiladores",
    "enfriador", "enfriadores", "cooler",
}

# "pantalla" es sinónimo de "televisor" (real: "Pantalla Samsung 75\" LED"),
# pero también aparece en specs de relojes, celulares, tablets y laptops
# ("Pantalla 1.6\"", "Pantalla De 6.7\""). Sin esto, un Galaxy Watch o un
# celular terminan contando como "el televisor más barato". Namespaced: solo
# se aplica cuando la query es de TV, para no afectar búsquedas de esos
# productos en sí ("celular samsung", "reloj samsung", etc.).
_TV_TOKENS = next(g for g in _SYN_GROUPS if "televisor" in g)
# Otros aparatos que mencionan "pantalla" en su ficha: relojes, celulares,
# tablets, laptops, cámaras y timbres con video. El nombre ya no es solo
# "wearable" porque el problema es la pantalla, no lo que se lleva puesto.
_SCREEN_DEVICE_EXCLUDE = {
    "reloj", "relojes", "smartwatch", "watch", "band", "bandas",
    "pulsera", "pulseras", "buds", "fit", "wearable",
    "celular", "celulares", "telefono", "telefonos", "smartphone",
    "smartphones", "tablet", "tableta", "tabletas", "tablets", "ipad",
    "laptop", "laptops", "notebook", "notebooks",
    "camara", "camaras", "webcam", "videoportero", "videollamadas",
    "timbre", "timbres", "intercomunicador", "monitor", "monitores",
}

# "pantalla" también es la tulipa de una lámpara ("Lámpara Colgante 1 Luz
# Satín Níquel Pantalla Opal Blanco" llegó a ser el televisor más barato a
# Q149), y la superficie de un proyector o de una careta. Mismo tratamiento
# que _SCREEN_DEVICE_EXCLUDE: solo aplica si la query es de televisor.
_LIGHTING_EXCLUDE = {
    "lampara", "lamparas", "colgante", "colgantes", "candil", "candiles",
    "candelabro", "candelabros", "foco", "focos", "bombillo", "bombillos",
    "luminaria", "luminarias", "plafon", "plafones", "arbotante",
    "arbotantes", "farol", "faroles", "tulipa", "tulipas",
    "proyeccion", "facial", "careta", "caretas",
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
    r"\baire(?:s)?\s+acondicionado(?:s)?\b|\bair\s+conditioner\b|\bac\s+portatil\b"
    r"|\bportable\s+ac\b|\bmini\s*split\b",
    re.I,
)

# Accesorios propios de aires acondicionados (namespaced: solo se usan en la
# rama de _is_air_conditioner_query, no en el set _ACCESSORY global, para no
# afectar búsquedas no relacionadas con AC).
_AC_ACCESSORY_EXTRA = {
    "foam", "panel", "panels", "insulated", "insulation", "insulator",
    "sealing", "weatherstrip", "weatherstripping", "strip", "strips",
    "gasket", "gaskets", "hose", "hoses", "drain", "tubing", "tube", "tubes",
    "bracket", "brackets", "cover", "covers",
    "espray", "aerosol", "desinfectante", "desinfectantes", "limpiador",
    "limpiadores", "llave", "llaves", "desconexion", "interruptor",
    "interruptores", "breaker", "capacitor", "capacitores", "contactor",
    "contactores", "refrigerante", "gas", "manometro", "manometros",
    "ducto", "ductos", "ducteria", "marco", "marcos", "decorativo",
    "decorativa", "rejilla", "rejillas", "salida", "salidas",
}


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
                return out

    # Fallback para queries genéricas de 2+ palabras sin alias conocido
    # ("televisor samsung"): varias tiendas devuelven basura (o nada) para la
    # frase completa aunque sí tengan el producto, porque su buscador no hace
    # bien el AND de dos términos. Agregamos cada palabra suelta como query
    # adicional para ampliar lo que se trae de cada tienda; el filtro de
    # relevancia sigue exigiendo que el nombre final calce con TODAS las
    # palabras originales, así que esto no afloja qué cuenta como match.
    if len(base) >= 2:
        for tok in base:
            if _is_number(tok) or len(tok) < 3:
                continue
            if normalize(tok) not in seen:
                seen.add(normalize(tok))
                out.append(tok)
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
        # "mini split" es el nombre común (y el que traen los propios títulos)
        # de los aires acondicionados split; sin esto, palabras del resto del
        # título (p.ej. "Filtro HD") disparan la exclusión de accesorios y el
        # producto desaparece aunque sí sea un mini split.
        or {"mini", "split"} <= qtoks
        or "minisplit" in qtoks
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
    is_ac_query = _is_air_conditioner_query(query)
    if is_ac_query:
        # Camino propio: exigir el literal "mini"+"split" en el título deja
        # afuera unidades reales que el vendedor solo describe como "Aire
        # Acondicionado ... 12,000 BTU" sin escribir "mini split". Aceptamos
        # cualquier título AC-positivo y solo filtramos accesorios reales
        # (por frase "para X" y por si el accesorio encabeza el título).
        if not _air_conditioner_query_matches(original_qtoks, name_norm, name_toks):
            return False
        allows_for_phrase = _allows_for_phrase(query)
        if not allows_for_phrase and re.search(
            r"\bpara\s+(?:el\s+|la\s+|tu\s+)?(?:mini\s*split|aire(?:\s+acondicionado)?|a/?c)\b"
            r"|\bpara\s+uso\s+con\s+(?:el\s+|la\s+|tu\s+)?(?:mini\s*split|aire(?:\s+acondicionado)?|a/?c)\b",
            name_norm,
        ):
            return False
        # Ventana más ancha que el chequeo genérico: los listados de
        # accesorios de AC suelen anteponer "Marca + tipo de equipo
        # compatible" antes de nombrar el accesorio en sí (p.ej. "BJADE'S
        # Window Air Conditioner Side Insulated Foam Panel"). Namespaced a
        # este branch para no afectar otras búsquedas (no es _ACCESSORY global).
        head = tokens(name_norm)[:8]
        if (_ACCESSORY | _AC_ACCESSORY_EXTRA) & set(head):
            return False
        return True
    if _is_owala_query(query):
        return _owala_query_matches(name_toks)

    query_wants_accessory = any(t in _ACCESSORY for t in original_qtoks) or _is_brand_query(query)
    allows_for_phrase = _allows_for_phrase(query)
    if not query_wants_accessory:
        if _ACCESSORY & name_toks:
            return False
        is_tv_query = bool(set(original_qtoks) & _TV_TOKENS)
        wants_other_device = bool(set(original_qtoks) & _SCREEN_DEVICE_EXCLUDE)
        if is_tv_query and not wants_other_device and (_SCREEN_DEVICE_EXCLUDE & name_toks):
            return False
        wants_lighting = bool(set(original_qtoks) & _LIGHTING_EXCLUDE)
        if is_tv_query and not wants_lighting and (_LIGHTING_EXCLUDE & name_toks):
            return False
        # La versión de juguete de un aparato siempre gana el puesto de "más
        # barato" y desprestigia el resultado principal.
        qtok_set = set(original_qtoks)
        if (
            qtok_set & _TOY_SENSITIVE_TOKENS
            and not (qtok_set & _TOY_MARKERS)
            and (_TOY_MARKERS & name_toks)
        ):
            return False
        # "portatil" es adjetivo: si el título nombra otra categoría, no es laptop.
        if (
            qtok_set & _LAPTOP_TOKENS
            and not (qtok_set & _LAPTOP_EXCLUDE)
            and (_LAPTOP_EXCLUDE & name_toks)
        ):
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
