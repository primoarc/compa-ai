"""Revisa el robots.txt de cada tienda contra las rutas que usamos.

    python scripts/check_robots.py

Evalúa según RFC 9309 (grupo "*", comodines, gana la regla más larga y a igual
largo "Allow"). El parser de la librería estándar se equivoca con Steren y EPA.
Sale con código 1 si alguna ruta en uso está prohibida.
"""

from __future__ import annotations

import re
import sys

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")

# (tienda, host, ruta, uso). Solo rutas que el código pide hoy.
CHECKS = [
    ("siman", "gt.siman.com", "/api/catalog_system/pub/products/search?fq=C:1", "ingesta y búsqueda"),
    ("cemaco", "www.cemaco.com", "/api/catalog_system/pub/products/search?fq=C:1", "ingesta y búsqueda"),
    ("walmart", "www.walmart.com.gt", "/api/catalog_system/pub/products/search?fq=C:1", "ingesta y búsqueda"),
    ("max", "ac.cnstrc.com", "/browse/group_id/1", "ingesta"),
    ("max", "ac.cnstrc.com", "/search/televisor", "búsqueda"),
    ("curacao", "www.lacuracaonline.com", "/guatemala/c/electrodomesticos?p=2", "ingesta"),
    ("radioshack", "www.radioshackla.com", "/guatemala/c/audio?p=2", "ingesta"),
    ("steren", "www.steren.com.gt", "/audio/audifonos.html", "ingesta (solo página 1)"),
    ("epa", "gt.epaenlinea.com", "/productos/herramientas.html?p=2", "ingesta"),
    ("kemik", "www.kemik.gt", "/audio?page=2", "ingesta"),
    ("kemik", "www.kemik.gt", "/search?query=tv", "búsqueda"),
    ("intelaf", "api.intelaf.com:2053", "/app/api/producto/busqueda", "ingesta y búsqueda"),
    ("pricesmart", "core.dxpapi.com", "/api/v1/core/?q=tv", "ingesta y búsqueda"),
    ("novex", "eu1-search.doofinder.com", "/5/search?query=tv", "ingesta y búsqueda"),
    ("sears", "sears.com.gt", "/wp-json/wc/store/products?per_page=100", "ingesta"),
    ("sears", "sears.com.gt", "/wp-json/wc/store/products?search=tv", "búsqueda"),
    ("sears", "sears.com.gt", "/page/2/?post_type=product", "ingesta (respaldo HTML)"),
]


def groups(text: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    out: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    last = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if last == "rule":
                out.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last = "agent"
        elif key in ("allow", "disallow"):
            rules.append((key, value))
            last = "rule"
    if agents:
        out.append((agents, rules))
    return out


def _pattern(rule: str) -> re.Pattern:
    anchored = rule.endswith("$")
    rule = rule[:-1] if anchored else rule
    return re.compile("^" + ".*".join(re.escape(p) for p in rule.split("*")) + ("$" if anchored else ""))


def verdict(text: str, path: str) -> tuple[bool, str]:
    rules = [r for agents, rs in groups(text) if "*" in agents for r in rs]
    best = None
    for kind, rule in rules:
        if rule and _pattern(rule).match(path):
            key = (len(rule), kind == "allow")
            if best is None or key > best[0]:
                best = (key, kind, rule)
    if best is None:
        return True, "sin regla"
    return best[1] == "allow", f"{best[1]}: {best[2]}"


def main() -> int:
    cache: dict[str, tuple[str, str]] = {}
    blocked = 0
    for store, host, path, use in CHECKS:
        if host not in cache:
            try:
                r = httpx.get(f"https://{host}/robots.txt", headers={"User-Agent": UA},
                              timeout=15, follow_redirects=True)
                plain = r.status_code == 200 and "html" not in r.headers.get("content-type", "")
                cache[host] = (str(r.status_code), r.text if plain else "")
            except httpx.HTTPError as exc:
                cache[host] = (type(exc).__name__, "")
        status, text = cache[host]
        ok, why = verdict(text, path) if text else (True, "sin robots.txt")
        blocked += not ok
        print(f"{store:11} {use:26} {'permitido' if ok else 'PROHIBIDO':10} {status:4} {why}  {host}{path}")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
