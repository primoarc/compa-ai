"""Definición de tiendas y carga de configuración del usuario.

Cada tienda VTEX expone la misma API pública de catálogo sin auth. La única
diferencia entre tiendas es el dominio. Tiendas no-VTEX (Kemik, Novex) se
agregarán en el futuro vía scraper.py.

Si una tienda empieza a bloquear con bot-detection, comentarla aquí y
documentar el workaround en el README.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.getenv("GT_COMPARE_CONFIG_DIR", Path.home() / ".gt-compare"))
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
    Store("siman", "Siman", "gt.siman.com"),

    # Max usa Constructor.io para búsqueda pública. Ver scraper.fetch_max_constructor.
    Store("max", "Max Distelsa", "www.max.com.gt", kind="max"),
    # --- Magento (Grupo Unicomer), scraping HTML de /guatemala/search/{q} ---
    # GraphQL está deshabilitado en estos sitios; se parsea el listado HTML.
    Store("curacao", "La Curacao", "www.lacuracaonline.com",
          kind="magento", search_path="/guatemala/search/"),
    Store("radioshack", "RadioShack", "www.radioshackla.com",
          kind="magento", search_path="/guatemala/search/"),
    Store("steren", "Steren", "www.steren.com.gt",
          kind="magento", search_path="/catalogsearch/result/?q="),
    Store("epa", "EPA", "gt.epaenlinea.com",
          kind="magento", search_path="/catalogsearch/result/?q="),

    # --- Kemik: Next.js con SSR; se scrapea el HTML de /search?query={q} ---
    Store("kemik", "Kemik", "www.kemik.gt", kind="kemik"),

    # --- Intelaf: API pública usada por su frontend Next.js ---
    Store("intelaf", "Intelaf", "www.intelaf.com", kind="intelaf"),

    # --- Novex: Doofinder, el motor público usado por su buscador ---
    Store("novex", "Novex", "www.novex.com.gt", kind="novex"),

    # --- Sears Guatemala: WooCommerce/WordPress ---
    Store("sears", "Sears", "sears.com.gt", kind="woocommerce"),

    # PriceSmart: Bloomreach Discovery. El precio está en campos por país+club
    # (price_GT_6303, en centavos). Ver scraper.fetch_pricesmart.
    Store("pricesmart", "PriceSmart", "www.pricesmart.com", kind="pricesmart"),
]


def _default_stores_config() -> list[dict]:
    return [
        {"key": s.key, "name": s.name, "domain": s.domain,
         "kind": s.kind, "enabled": s.enabled, "search_path": s.search_path}
        for s in DEFAULT_STORES
    ]


DEFAULT_CONFIG = {
    "stores": _default_stores_config(),
    "timeout_seconds": 8,
    "cache_minutes": 30,
}


def _merge_default_config(cfg: dict) -> dict:
    """Agrega tiendas nuevas al config existente sin pisar preferencias."""
    if not isinstance(cfg, dict):
        return DEFAULT_CONFIG

    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    current = list(cfg.get("stores") or [])
    defaults_by_key = {s["key"]: s for s in _default_stores_config()}
    for store in current:
        if not isinstance(store, dict):
            continue
        # Migración del default anterior: Max estaba deshabilitado como VTEX por
        # WAF. Ahora se consulta por Constructor.io y debe quedar activo.
        if (
            store.get("key") == "max"
            and store.get("domain") == "www.max.com.gt"
            and store.get("kind", "vtex") == "vtex"
            and store.get("enabled") is False
        ):
            store.update(defaults_by_key["max"])
    seen = {s.get("key") for s in current if isinstance(s, dict)}
    for store in defaults_by_key.values():
        if store["key"] not in seen:
            current.append(store)
    merged["stores"] = current
    return merged


def ensure_config() -> dict:
    """Crea el config.yaml por defecto si no existe y lo devuelve parseado."""
    if os.getenv("VERCEL") and not os.getenv("GT_COMPARE_CONFIG_DIR"):
        return DEFAULT_CONFIG
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            with CONFIG_FILE.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(DEFAULT_CONFIG, fh, allow_unicode=True, sort_keys=False)
            return DEFAULT_CONFIG
        with CONFIG_FILE.open(encoding="utf-8") as fh:
            cfg = _merge_default_config(yaml.safe_load(fh) or DEFAULT_CONFIG)
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
        return cfg
    except OSError:
        return DEFAULT_CONFIG


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
