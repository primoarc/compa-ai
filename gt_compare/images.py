"""Miniaturas: pedirle a la CDN de cada tienda la imagen al tamaño que se muestra.

Probado el 28-sep-2026:
  - VTEX (Siman, Cemaco, Walmart): /arquivos/ids/{id}-{w}-{h}/ → 1000 px, 85 KB pasan a 2,4 KB.
  - Max (Magento con optimizador): ?width={w} → 21 KB pasan a 3 KB.
Probado el 27-nov-2026:
  - La Curacao y RadioShack: ?width={w} → la imagen de 1000 px (517 KB) pasa a 8 KB.
El resto se deja como viene: Kemik ya da 300 px, y en PriceSmart, Intelaf y Novex
no hay parámetro de tamaño (entre 5 y 35 KB).
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

_VTEX = re.compile(r"(\.vteximg\.com\.br/arquivos/ids/)(\d+)(?:-\d+-(?:\d+|auto))?/")
# CDN que aceptan ?width=
_WIDTH_HOSTS = {"backoffice.max.com.gt", "www.lacuracaonline.com", "www.radioshackla.com"}


def thumb(url: Optional[str], px: int) -> Optional[str]:
    """URL de la imagen a `px` de ancho si la CDN lo permite; si no, la original."""
    if not url:
        return url
    if _VTEX.search(url):
        return _VTEX.sub(lambda m: f"{m.group(1)}{m.group(2)}-{px}-{px}/", url, count=1)
    parts = urlsplit(url)
    if parts.netloc in _WIDTH_HOSTS:
        return urlunsplit(parts._replace(query=f"width={px}"))
    return url
