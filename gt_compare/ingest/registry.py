"""Qué enumerador usa cada tienda."""

from __future__ import annotations

from typing import Optional

from ..stores import Store
from .types import Enumerator
from .intelaf import enumerate_intelaf
from .kemik import enumerate_kemik
from .magento import enumerate_magento
from .max import enumerate_max
from .novex import enumerate_novex
from .pricesmart import enumerate_pricesmart
from .sears import enumerate_sears
from .vtex import enumerate_vtex

# Por plataforma: todas las tiendas VTEX comparten enumerador.
BY_KIND: dict[str, Enumerator] = {"vtex": enumerate_vtex, "magento": enumerate_magento}

# Por tienda: plataformas donde cada sitio necesita su propio recorrido.
BY_STORE: dict[str, Enumerator] = {
    "max": enumerate_max,
    "pricesmart": enumerate_pricesmart,
    "sears": enumerate_sears,
    "novex": enumerate_novex,
    "intelaf": enumerate_intelaf,
    "kemik": enumerate_kemik,
}


def for_store(store: Store) -> Optional[Enumerator]:
    return BY_STORE.get(store.key) or BY_KIND.get(store.kind)
