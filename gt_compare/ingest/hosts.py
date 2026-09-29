"""Dónde se ingiere cada tienda: GitHub Actions o la Mac (launchd).

Desde los servidores de GitHub algunas tiendas no responden (Kemik devuelve 403
a cualquier IP de datacenter). Esas corren en la Mac del dueño; el resto en
Actions. Las dos listas salen de LOCAL_STORES, así ninguna tienda se ingiere en
los dos lados ni queda sin dueño (tests/test_hosts.py lo comprueba).
"""

from __future__ import annotations

from ..stores import load_stores

# Tiendas que GitHub no alcanza y se ingieren desde la Mac. Ver docs/ingesta-programada.md.
#   kemik: 403 en todas las páginas desde IPs de datacenter (Cloudflare).
# La Curacao y RadioShack pasan aquí solo si en Actions leen menos del 95% de
# lo que declaran y desde la Mac llegan al 95% (regla del dueño, 28-sep).
LOCAL_STORES = frozenset({"kemik"})

WHERE = ("actions", "local")


def all_stores() -> list[str]:
    return [s.key for s in load_stores()]


def stores_for(where: str) -> list[str]:
    """Tiendas que le tocan a `actions` o a `local`, en el orden de stores.yaml."""
    if where not in WHERE:
        raise ValueError(f"where debe ser uno de {WHERE}: {where!r}")
    return [k for k in all_stores() if (k in LOCAL_STORES) == (where == "local")]
