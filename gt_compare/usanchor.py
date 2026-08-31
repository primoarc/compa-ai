"""Precio de EE.UU. como referencia externa.

Todas las demás señales del proyecto son internas: el precio de lista lo pone
la tienda y lo puede inflar, y comparar entre tiendas guatemaltecas no sirve si
todas están caras a la vez. El precio de EE.UU. no se puede inflar desde acá.

La pregunta que responde no es "¿el margen del importador es justo?" sino la
que uno realmente se hace: **¿lo compro aquí o me lo traigo?**

    <= 1.00x   más barato que en EE.UU.
    <= 1.20x   comprá aquí, no vale el enredo de importarlo
    <= 1.50x   depende de cuánto lo querés ya
     > 1.50x   traelo o esperá

Por ahora la fuente es Apple, que publica su catálogo de EE.UU. como JSON
estructurado y no bloquea. Los retailers (Best Buy, Walmart, Target, Newegg,
B&H) bloquean o exigen llave de API; Amazon responde pero devuelve precios sin
título que no son fiables.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

FX_URL = "https://open.er-api.com/v6/latest/USD"
APPLE_PAGES = {
    "ipad-air": "https://www.apple.com/shop/buy-ipad/ipad-air",
    "ipad": "https://www.apple.com/shop/buy-ipad/ipad",
    "ipad-pro": "https://www.apple.com/shop/buy-ipad/ipad-pro",
    "ipad-mini": "https://www.apple.com/shop/buy-ipad/ipad-mini",
}

_PRODUCTS_RE = re.compile(r'"products"\s*:\s*(\[.*?\])\s*,\s*"', re.S)
_CAP_RE = re.compile(r"\b(\d+)\s*(GB|TB)\b", re.I)
_INCH_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:-?inch|\"|”|pulgadas?)", re.I)


def tipo_de_cambio() -> float:
    """Quetzales por dólar. Se consulta en vivo para no clavar un número."""
    r = httpx.get(FX_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    tasa = (r.json().get("rates") or {}).get("GTQ")
    if not tasa:
        raise RuntimeError("no vino la tasa GTQ")
    return float(tasa)


def _capacidad_gb(texto: str) -> Optional[int]:
    m = _CAP_RE.search(texto or "")
    if not m:
        return None
    val = int(m.group(1))
    return val * 1024 if m.group(2).upper() == "TB" else val


def _pulgadas(texto: str) -> Optional[float]:
    m = _INCH_RE.search(texto or "")
    return float(m.group(1)) if m else None


def catalogo_apple() -> list:
    """Productos de Apple EE.UU. con su precio de lista, desde apple.com."""
    salida: list = []
    vistos: set = set()
    with httpx.Client(follow_redirects=True, timeout=30) as c:
        for url in APPLE_PAGES.values():
            try:
                h = c.get(url, headers=HEADERS).text
            except Exception:  # noqa: BLE001
                continue
            for m in _PRODUCTS_RE.finditer(h):
                try:
                    productos = json.loads(m.group(1))
                except ValueError:
                    continue
                for p in productos:
                    precio = ((p.get("price") or {}).get("fullPrice"))
                    nombre = p.get("name") or ""
                    if not precio or not nombre:
                        continue
                    # Un mismo modelo se repite por color: guardamos uno.
                    clave = (
                        re.sub(r"\s*-\s*[^-]+$", "", nombre).strip().lower(),
                        float(precio),
                    )
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    salida.append({
                        "nombre": nombre,
                        "usd": float(precio),
                        "gb": _capacidad_gb(nombre),
                        "pulgadas": _pulgadas(nombre),
                        "celular": bool(re.search(r"wi-?fi\s*\+\s*cellular", nombre, re.I)),
                    })
    return salida


# "Protector de pantalla para iPad" también contiene "ipad" y aparecía como
# 0.03x del precio de EE.UU.
_ACCESORIO_RE = re.compile(
    r"protector|funda|case|cover|teclado|keyboard|lapiz|pencil|stylus|"
    r"cable|cargador|adaptador|soporte|mica|vidrio|estuche|paquete de",
    re.I,
)


def es_accesorio(nombre: str) -> bool:
    return bool(_ACCESORIO_RE.search(nombre or ""))


def _familia(texto: str) -> Optional[str]:
    t = (texto or "").lower().replace("‑", "-")
    for fam in ("ipad pro", "ipad air", "ipad mini"):
        if fam in t:
            return fam
    if "ipad" in t:
        return "ipad"
    return None


def emparejar(nombre_gt: str, catalogo: list) -> Optional[dict]:
    """Producto de EE.UU. equivalente: misma familia, pulgadas y capacidad."""
    if es_accesorio(nombre_gt):
        return None
    fam = _familia(nombre_gt)
    if not fam:
        return None
    # Distinto chip es distinto producto: un M4 no se compara contra un M5.
    chip_gt = re.search(r"\bM(\d)\b", nombre_gt or "", re.I)
    gb = _capacidad_gb(nombre_gt)
    inch = _pulgadas(nombre_gt)
    # El GT rara vez dice "cellular"; se compara contra la versión Wi-Fi, que es
    # la más barata, para no inflar la referencia a favor nuestro.
    candidatos = [
        c for c in catalogo
        if _familia(c["nombre"]) == fam and not c["celular"]
        and (gb is None or c["gb"] == gb)
        and (inch is None or c["pulgadas"] is None or abs(c["pulgadas"] - inch) < 0.35)
    ]
    if not candidatos:
        return None
    return min(candidatos, key=lambda c: c["usd"])
