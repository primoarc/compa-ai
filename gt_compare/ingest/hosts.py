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
#   curacao, radioshack: 406 de Fastly a IPs de datacenter. Regla del dueño: 95% de
#   lo declarado. Actions 13,1% y 8,8%; Mac 99,9% y 99,9% (2-oct).
LOCAL_STORES = frozenset({"kemik", "curacao", "radioshack"})

WHERE = ("actions", "local")


def all_stores() -> list[str]:
    return [s.key for s in load_stores()]


def stores_for(where: str) -> list[str]:
    """Tiendas que le tocan a `actions` o a `local`, en el orden de stores.yaml."""
    if where not in WHERE:
        raise ValueError(f"where debe ser uno de {WHERE}: {where!r}")
    return [k for k in all_stores() if (k in LOCAL_STORES) == (where == "local")]
