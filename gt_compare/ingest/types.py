"""Contrato común de los enumeradores de catálogo."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, Protocol

from ..stores import Store
from .http import PoliteClient


@dataclass
class ProductRecord:
    """Un SKU de una tienda, tal como lo publica en una corrida."""

    store_key: str
    store_sku: str            # id estable del SKU en la tienda (itemId, sku, slug)
    url: str
    name: str
    price: Optional[float]
    list_price: Optional[float] = None
    available: Optional[int] = None   # >0 disponible, 0 agotado; None si la tienda no lo dice
    # Si la tienda da un precio distinto solo en efectivo, `price` es el precio
    # con cualquier medio de pago (comparable entre tiendas) y este es el de contado.
    cash_price: Optional[float] = None
    brand: Optional[str] = None
    ean: Optional[str] = None
    image: Optional[str] = None
    store_category: Optional[str] = None
    promo_text: Optional[str] = None  # promociones/condiciones (p.ej. "con tarjeta X")
    first_sku: bool = True            # primer SKU del producto (para heredar historial viejo)
    extra: dict = field(default_factory=dict)


@dataclass
class EnumerationStats:
    pages: int = 0
    records: int = 0
    errors: int = 0
    coverage_note: str = ""   # p.ej. "catálogo completo" o "parcial: 40 búsquedas"


class Enumerator(Protocol):
    """Recorre el catálogo de una tienda.

    Debe usar solo `client` para las peticiones (así se aplican los límites de
    ritmo y el backoff), producir registros a medida que los obtiene, y anotar
    en `stats` qué tan completa fue la cobertura.
    """

    def __call__(
        self, store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
    ) -> AsyncIterator[ProductRecord]:
        ...
