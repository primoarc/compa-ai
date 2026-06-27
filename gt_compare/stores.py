"""Definición de tiendas y carga de configuración del usuario.

Cada tienda VTEX expone la misma API pública de catálogo sin auth. La única
diferencia entre tiendas es el dominio. Tiendas no-VTEX (Kemik, Novex) se
agregarán en el futuro vía scraper.py.

Si una tienda empieza a bloquear con bot-detection, comentarla aquí y
documentar el workaround en el README.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".gt-compare"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


@dataclass
class Store:
    key: str           # identificador corto usado en --store
    name: str          # nombre legible para mostrar
    domain: str        # dominio (sin esquema)
    kind: str = "vtex"  # "vtex" | "magento" | "scraper"
    enabled: bool = True
    # Para tiendas Magento: prefijo de la URL de búsqueda (el query se anexa).
    search_path: str | None = None


# Tiendas VTEX Legacy con API pública. Si alguna bloquea requests por
# bot-detection, comentar la línea y anotar el workaround en el README.
DEFAULT_STORES: list[Store] = [
    # --- VTEX verificadas y funcionando ---
    Store("cemaco", "Cemaco", "www.cemaco.com"),
    Store("walmart", "Walmart Guatemala", "www.walmart.com.gt"),

    # --- VTEX bloqueadas / no alcanzables (deshabilitadas, ver README) ---
    # Max Distelsa: WAF (Cloudflare/Akamai) devuelve 403 en TODOS los endpoints
    # y con UA de app móvil. Bloqueo en el edge, no se evade server-side.
    # Workaround: intercept de app móvil (ver scraper.fetch_max + config
    # max_headers) o Playwright headless. Ver README.
    Store("max", "Max Distelsa", "www.max.com.gt", enabled=False),
    # --- Magento (Grupo Unicomer), scraping HTML de /guatemala/search/{q} ---
    # GraphQL está deshabilitado en estos sitios; se parsea el listado HTML.
    Store("curacao", "La Curacao", "www.lacuracaonline.com",
          kind="magento", search_path="/guatemala/search/"),
    Store("radioshack", "RadioShack", "www.radioshackla.com",
          kind="magento", search_path="/guatemala/search/"),

    # --- Kemik: Next.js con SSR; se scrapea el HTML de /search?query={q} ---
    Store("kemik", "Kemik", "www.kemik.gt", kind="kemik"),

    # PriceSmart: Bloomreach Discovery. El precio está en campos por país+club
    # (price_GT_6303, en centavos). Ver scraper.fetch_pricesmart.
    Store("pricesmart", "PriceSmart", "www.pricesmart.com", kind="pricesmart"),

    # --- Tiendas no-VTEX pendientes (ver scraper.py) ---
    # Store("novex", "Novex", "www.novex.com.gt", kind="scraper", enabled=False),
]


DEFAULT_CONFIG = {
    "stores": [
        {"key": s.key, "name": s.name, "domain": s.domain,
         "kind": s.kind, "enabled": s.enabled, "search_path": s.search_path}
        for s in DEFAULT_STORES
    ],
    "timeout_seconds": 8,
    "cache_minutes": 30,
}


def ensure_config() -> dict:
    """Crea el config.yaml por defecto si no existe y lo devuelve parseado."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(DEFAULT_CONFIG, fh, allow_unicode=True, sort_keys=False)
        return DEFAULT_CONFIG
    with CONFIG_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or DEFAULT_CONFIG


def load_stores(only: str | None = None) -> list[Store]:
    """Devuelve las tiendas habilitadas según config.yaml.

    `only` filtra por la key de una tienda específica (--store).
    """
    cfg = ensure_config()
    stores: list[Store] = []
    for raw in cfg.get("stores", []):
        store = Store(
            key=raw["key"],
            name=raw.get("name", raw["key"]),
            domain=raw["domain"],
            kind=raw.get("kind", "vtex"),
            enabled=raw.get("enabled", True),
            search_path=raw.get("search_path"),
        )
        if only:
            # Override explícito: el usuario fuerza una tienda con --store,
            # aunque esté deshabilitada por defecto.
            if store.key == only:
                stores.append(store)
            continue
        if not store.enabled:
            continue
        stores.append(store)
    return stores
