"""Detección de "mismo producto" entre tiendas.

El comparador agrupa por tienda, así que una fila con el televisor más barato
de Kemik y otra con el de Siman se ven como una comparación de precios cuando
en realidad son productos distintos (32" contra 75"). Mostrar "+Q4,350" ahí es
engañoso.

Este módulo extrae del título dos señales baratas y sorprendentemente fiables
en el catálogo guatemalteco:

  * el código de modelo ("UN32H5000FPXPA", "MTTBBL099", "BN701LA"), que las
    tiendas copian del fabricante y por eso cruza entre sitios; y
  * el tamaño en pulgadas, necesario porque un mismo código de serie puede
    cubrir varias diagonales ("U8000H" existe en 43" y en 85").

Con eso se detecta el caso que de verdad le importa al usuario: el MISMO
televisor a Q1,397 en La Curacao y a Q2,399 en RadioShack.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

# Tokens que parecen código de modelo pero son especificaciones. Se descartan
# por forma, no por lista, para que no haya que mantener un diccionario.
_RES_PAIR = re.compile(r"^\d+X\d+$")            # 3840X2160
_DIGITS_ONE_LETTER = re.compile(r"^\d+[A-Z]$")  # 1080P, 700W, 60P
_SPEC_SUFFIX = re.compile(
    r"^\d+(?:BTU|BTUS|W|WATT|WATTS|HZ|ML|L|LT|OZ|GB|TB|MB|MAH|V|VOLT|VOLTS"
    r"|K|P|IN|CM|MM|KG|G|PPP|DPI|NITS|LM)$"
)
# Especificaciones con forma "letras+dígitos" que sí pasarían el filtro general.
_SPEC_TOKENS = {
    "HDR10", "HDR10PLUS", "DVBT2", "DVBS2", "DVBC", "H264", "H265",
    "USB2", "USB3", "WIFI5", "WIFI6", "WIFI4", "BLE5", "AC3",
    "MPEG4", "VESA200", "HDMI2", "HDMI21", "DDR4", "DDR5",
}

_TOKEN_RE = re.compile(r"[A-Z0-9]+")

# 43", 43 pulgadas, 55 plg. Se exige la unidad para no capturar centímetros
# ("140 cm / 55 Pulgadas" debe dar 55, no 140).
_SIZE_RE = re.compile(
    r"(\d{2}(?:\.\d)?)\s*(?:\"|”|″|''|´´|pulg(?:ada)?s?|plg\b)",
    re.I,
)


# Marcas: si dos títulos declaran marcas distintas no son el mismo producto,
# por más que compartan un código. Es la guarda de mayor precisión y la más
# barata de mantener.
_BRANDS = {
    "samsung", "lg", "sony", "tcl", "hisense", "xiaomi", "toshiba", "philips",
    "panasonic", "compaq", "roku", "vizio",
    "dell", "hp", "lenovo", "acer", "asus", "apple", "microsoft", "huawei",
    "motorola", "msi", "gateway",
    "ninja", "oster", "taurus", "mabe", "whirlpool", "mastertech", "holstein",
    "cuisinart", "cuisiniart", "nutribullet", "mertec", "morphy", "hamilton",
    "electrolux", "frigidaire", "atvio", "sankey",
    "skullcandy", "jbl", "bose", "sennheiser", "logitech", "steren",
    "dewalt", "makita", "bosch", "truper", "stanley", "milwaukee", "black",
}

# Sufijo de modelo de CPU: "i5-1334U", "Ryzen 5 7520U", "Core 3-100U". Estos
# tokens parecen código de producto pero identifican el procesador, y por eso
# cruzaban laptops de marcas distintas.
_CPU_RE = re.compile(
    # El sufijo puede terminar en letra+dígito ("1135G7"), de ahí el \d? final.
    # Los separadores admiten cualquier cosa que no sea alfanumérica porque los
    # títulos traen "Ryzen™ 3 7320U", "Intel® Core™ i5-1334U", guiones largos…
    r"\bI([3579])[^A-Z0-9]{0,3}(\d{3,5}[A-Z]{0,2}\d?)\b"
    r"|\bRYZEN[^A-Z0-9]{0,3}(\d)[^A-Z0-9]{0,3}(\d{4}[A-Z]{0,2}\d?)\b"
    r"|\bCORE[^A-Z0-9]{0,3}(\d)[^A-Z0-9]{0,3}(\d{3,5}[A-Z]{0,2}\d?)\b",
    re.I,
)

# Capacidades declaradas (RAM y almacenamiento). Si ambos títulos las declaran
# y no coinciden, es otra configuración del mismo chasis, no el mismo producto.
_CAP_RE = re.compile(r"(\d{1,4})\s*(GB|TB|GIGABYTES|TERABYTES)\b", re.I)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _looks_like_model(tok: str) -> bool:
    """¿El token parece un código de modelo del fabricante?"""
    if len(tok) < 5 or tok in _SPEC_TOKENS:
        return False
    digits = sum(c.isdigit() for c in tok)
    letters = sum(c.isalpha() for c in tok)
    # Al menos 3 dígitos evita "HDR10"/"HDR10PLUS"; al menos 1 letra evita años
    # y capacidades sueltas.
    if digits < 3 or letters < 1:
        return False
    if _RES_PAIR.match(tok) or _DIGITS_ONE_LETTER.match(tok) or _SPEC_SUFFIX.match(tok):
        return False
    return True


def model_codes(name: str) -> set:
    """Códigos de modelo candidatos dentro del título."""
    upper = _strip_accents(name or "").upper()
    cpu_tokens = {
        g.upper() for m in _CPU_RE.finditer(upper) for g in m.groups() if g
    }
    return {
        t for t in _TOKEN_RE.findall(upper)
        if _looks_like_model(t) and t not in cpu_tokens
    }


def brands(name: str) -> set:
    """Marcas reconocidas en el título."""
    lower = _strip_accents(name or "").lower()
    return {t for t in re.findall(r"[a-z]+", lower) if t in _BRANDS}


def cpu_model(name: str) -> str:
    """Procesador declarado, normalizado ("I5-1334U"), o cadena vacía."""
    m = _CPU_RE.search(_strip_accents(name or "").upper())
    if not m:
        return ""
    groups = [g for g in m.groups() if g]
    return "-".join(groups).upper() if len(groups) == 2 else ""


def capacities(name: str) -> tuple:
    """Capacidades declaradas en GB, en orden de aparición (RAM, disco…)."""
    out = []
    for num, unit in _CAP_RE.findall(_strip_accents(name or "").upper()):
        try:
            val = int(num)
        except ValueError:
            continue
        out.append(val * 1024 if unit.startswith("T") else val)
    return tuple(out)


def screen_size(name: str) -> Optional[float]:
    """Diagonal en pulgadas, si el título la declara."""
    for raw in _SIZE_RE.findall(name or ""):
        try:
            val = float(raw)
        except ValueError:
            continue
        if 10 <= val <= 120:
            return val
    return None


def _codes_overlap(a: set, b: set) -> Optional[str]:
    """Código compartido, aceptando que una tienda use el código extendido.

    "H5000F" (Kemik) está contenido en "UN32H5000FPXPA" (La Curacao): es el
    mismo aparato con el sufijo regional del distribuidor.
    """
    best = None
    for x in a:
        for y in b:
            if x == y or x in y or y in x:
                shared = x if len(x) <= len(y) else y
                if best is None or len(shared) > len(best):
                    best = shared
    return best


def _same_product(p: dict, q: dict) -> Optional[str]:
    """Mismo código Y ninguna especificación declarada que se contradiga.

    Compartir código no basta: "DC15250" es el chasis de un Dell que se vende
    con i3, i5 o i7 y con 8 o 16 GB, y "1135G7" es el procesador, que aparece
    igual en un HP, un Lenovo y un Dell. Sin estas guardas el comparador vuelve
    a inventar ahorros entre productos distintos.
    """
    shared = _codes_overlap(p["_codes"], q["_codes"])
    if not shared:
        return None
    # Marcas distintas declaradas: definitivamente no es el mismo producto.
    if p["_brands"] and q["_brands"] and not (p["_brands"] & q["_brands"]):
        return None
    # Un mismo código de serie puede abarcar varias diagonales.
    sp, sq = p["_size"], q["_size"]
    if sp is not None and sq is not None and sp != sq:
        return None
    if p["_cpu"] and q["_cpu"] and p["_cpu"] != q["_cpu"]:
        return None
    if p["_caps"] and q["_caps"] and p["_caps"] != q["_caps"]:
        return None
    return shared


def group_offers(items: Iterable[dict], *, min_stores: int = 2) -> list:
    """Agrupa ofertas de distintas tiendas que son el mismo producto.

    `items` son dicts con al menos name/price/store_key. Devuelve los grupos
    con al menos `min_stores` tiendas distintas, ordenados por cuánto se ahorra
    eligiendo bien (que es el dato que justifica existir al comparador).
    """
    pool = []
    for it in items:
        if not it.get("price"):
            continue
        codes = model_codes(it.get("name", ""))
        if not codes:
            continue
        name = it.get("name", "")
        pool.append({
            **it,
            "_codes": codes,
            "_size": screen_size(name),
            "_brands": brands(name),
            "_cpu": cpu_model(name),
            "_caps": capacities(name),
        })

    groups: list = []
    for cand in pool:
        for g in groups:
            # Contra todos los miembros, no solo el primero: una tienda puede
            # usar el código corto y otra el extendido, y el candidato podría
            # calzar con el segundo aunque no con el que abrió el grupo.
            if any(_same_product(m, cand) for m in g["members"]):
                g["members"].append(cand)
                break
        else:
            longest = max(cand["_codes"], key=len)
            groups.append({"model": longest, "members": [cand]})

    out = []
    for g in groups:
        # Una sola oferta por tienda: la más barata.
        by_store: dict = {}
        for m in g["members"]:
            k = m.get("store_key")
            if k not in by_store or m["price"] < by_store[k]["price"]:
                by_store[k] = m
        if len(by_store) < min_stores:
            continue
        offers = sorted(by_store.values(), key=lambda m: m["price"])
        label = max(
            (c for m in offers for c in m["_codes"]), key=len, default=g["model"]
        )
        spread = offers[-1]["price"] - offers[0]["price"]
        if spread <= 0:
            continue
        out.append({
            "model": label,
            "size": offers[0]["_size"],
            "stores": len(offers),
            "spread": round(spread, 2),
            "offers": [
                {k: v for k, v in o.items() if not k.startswith("_")} for o in offers
            ],
        })
    out.sort(key=lambda g: g["spread"], reverse=True)
    return out
