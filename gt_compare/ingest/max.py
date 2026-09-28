"""Enumerador de Max Distelsa (Constructor.io).

Constructor no pagina más allá de 10,000 resultados por consulta y los grupos
se traslapan (Ofertas, Marcas, Marketplace Backend), así que se recorre el
grupo raíz partido en rangos de precio que caben bajo ese tope. La categoría
de cada producto sale de sus propios grupos.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from ..scraper import _MAX_CONSTRUCTOR_KEY, _as_price, _max_available
from ..stores import Store
from .http import PoliteClient, StoreBlocked
from .types import EnumerationStats, ProductRecord

logger = logging.getLogger("gt_compare.ingest")

API = "https://ac.cnstrc.com"
ROOT_FALLBACK = "1339"  # "Tiendas MAX"
PER_PAGE = 200          # máximo que acepta Constructor
RESULT_CAP = 10_000     # más allá no devuelve resultados
PRICE_TOP = 100_000_000
# Cortes iniciales en quetzales; un rango que pase el tope se parte a la mitad.
BREAKS = [0, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000]
# Solo los campos que usamos: baja ~30% el peso de cada página.
FIELDS = [
    "id", "variation_id", "url", "url_key", "image_url", "final_price", "special_price",
    "price", "regular_price", "salable_quantity", "facets", "groups", "EAN", "brand",
    "meta_title", "shop_name",
]
BASE_PARAMS = {"key": _MAX_CONSTRUCTOR_KEY, "i": "gt-compare", "s": "1", "c": "ciojs-client-2.65.0"}


# --- parseo (sin red) -------------------------------------------------------

def parse_groups(payload: dict) -> tuple[list[tuple[str, int]], set[str]]:
    """(grupos raíz con su conteo, ids de categorías de nivel 2 tipo "Base")."""
    roots: list[tuple[str, int]] = []
    base: set[str] = set()
    for g in (payload.get("response") or {}).get("groups") or []:
        roots.append((str(g.get("group_id")), int(g.get("count") or 0)))
        for c in g.get("children") or []:
            if (c.get("data") or {}).get("category_type") == "Base":
                base.add(str(c.get("group_id")))
    return roots, base


def _facet(data: dict, name: str) -> Optional[str]:
    for f in data.get("facets") or []:
        if f.get("name") == name and f.get("values"):
            return str(f["values"][0]).strip() or None
    return None


def _ean(data: dict) -> Optional[str]:
    raw = data.get("EAN")
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    if isinstance(raw, (int, float)):
        # llega como número: se pierden los ceros a la izquierda de UPC-A/EAN-8
        code = str(int(raw))
        if len(code) in (7, 11):
            code = code.zfill(len(code) + 1)
        return code
    code = str(raw).strip()
    return code or None


def category_of(data: dict, base_ids: set[str]) -> Optional[str]:
    """Ruta más profunda bajo una categoría Base (no Ofertas/Marcas/Marketplace)."""
    best: list[str] = []
    for g in data.get("groups") or []:
        chain = list(g.get("path_list") or [])
        ids = [str(p.get("id")) for p in chain] + [str(g.get("group_id"))]
        names = [p.get("display_name") or "" for p in chain] + [g.get("display_name") or ""]
        if len(ids) < 2:
            continue
        top = ids[1]
        ok = top in base_ids if base_ids else top not in ("8784", "2158", "1727")
        if ok and len(names) - 1 > len(best):
            best = names[1:]  # sin "Tiendas MAX"
    return " > ".join(best) or None


def _record(store: Store, data: dict, name: Optional[str], sku: str, category: Optional[str],
            brand: Optional[str], first: bool) -> Optional[ProductRecord]:
    price = (
        _as_price(data.get("final_price"))
        or _as_price(data.get("special_price"))
        or _as_price(data.get("price"))
        or _as_price(data.get("regular_price"))
    )
    if not price:
        return None
    regular = _as_price(data.get("regular_price")) or _as_price(data.get("price"))
    url = data.get("url") or (f"https://{store.domain}/{data['url_key']}" if data.get("url_key") else "")
    extra = {"shop": data["shop_name"]} if data.get("shop_name") else {}
    return ProductRecord(
        store_key=store.key,
        store_sku=sku,
        url=url,
        name=(name or data.get("meta_title") or "").strip(),
        price=price,
        list_price=regular if regular and regular > price else None,
        available=_max_available(data),
        brand=brand,
        ean=_ean(data),
        image=data.get("image_url") or None,
        store_category=category,
        first_sku=first,
        extra=extra,
    )


def records_from_result(store: Store, item: dict, base_ids: set[str]) -> list[ProductRecord]:
    """Un registro por resultado, o por variación si Constructor las trae."""
    data = item.get("data") or {}
    pid = str(data.get("id") or "")
    if not pid:
        return []
    category = category_of(data, base_ids)
    brand = (data.get("brand") or "").strip() or _facet(data, "marca")
    out: list[ProductRecord] = []
    for var in item.get("variations") or []:
        # hereda precio/imagen del padre, pero no su EAN
        vdata = {**{k: v for k, v in data.items() if k != "EAN"}, **(var.get("data") or {})}
        vid = str(vdata.get("variation_id") or "")
        if not vid or vid == pid:
            continue
        rec = _record(store, vdata, var.get("value") or item.get("value"), vid,
                      category, brand, not out)
        if rec:
            out.append(rec)
    if not out:
        rec = _record(store, data, item.get("value"), pid, category, brand, True)
        if rec:
            out.append(rec)
    return out


# --- red --------------------------------------------------------------------

class _BadResponse(Exception):
    pass


class _Browser:
    def __init__(self, client: PoliteClient, stats: EnumerationStats):
        self.client = client
        self.stats = stats
        self.trim = True

    async def get(self, path: str, params: dict) -> dict:
        q = {**BASE_PARAMS, **params}
        if self.trim and path.startswith("/browse/group_id"):
            q["fmt_options[fields]"] = FIELDS
        resp = await self.client.get(API + path, params=q)
        self.stats.pages += 1
        if resp.status_code != 200:
            raise _BadResponse(f"HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise _BadResponse("JSON inválido") from exc
        results = (payload.get("response") or {}).get("results") or []
        if self.trim and results and not any((r.get("data") or {}).get("id") for r in results):
            # si el recorte de campos deja de funcionar, se pide completo
            logger.warning("max: fmt_options[fields] sin datos, se desactiva")
            self.trim = False
            return await self.get(path, params)
        return payload

    async def page(self, group: str, lo: float, hi: float, page: int) -> tuple[int, list[dict]]:
        payload = await self.get(f"/browse/group_id/{group}", {
            "page": page, "num_results_per_page": PER_PAGE, "filters[price]": f"{lo:.2f}-{hi:.2f}",
        })
        resp = payload.get("response") or {}
        # el conteo del grupo no está topado; total_num_results sí (10,000)
        total = int(resp.get("total_num_results") or 0)
        for g in resp.get("groups") or []:
            if str(g.get("group_id")) == group:
                total = max(total, int(g.get("count") or 0))
        return total, resp.get("results") or []


async def enumerate_max(
    store: Store, client: PoliteClient, stats: EnumerationStats, *, limit: int = 0
) -> AsyncIterator[ProductRecord]:
    br = _Browser(client, stats)
    seen: set[str] = set()
    ranges = declared_sum = capped = 0
    declared = 0
    cut = ""
    try:
        try:
            roots, base_ids = parse_groups(await br.get("/browse/groups", {}))
        except _BadResponse as exc:
            stats.errors += 1
            logger.warning("max: browse/groups falló (%s), se usa el grupo raíz conocido", exc)
            roots, base_ids = [], set()
        roots = roots or [(ROOT_FALLBACK, 0)]
        declared = sum(c for _, c in roots)

        for group, _ in roots:
            pending = [(float(BREAKS[i]) + (0.01 if i else 0), float(BREAKS[i + 1]))
                       for i in range(len(BREAKS) - 1)]
            pending.append((BREAKS[-1] + 0.01, float(PRICE_TOP)))
            while pending:
                lo, hi = pending.pop(0)
                try:
                    total, results = await br.page(group, lo, hi, 1)
                except _BadResponse as exc:
                    stats.errors += 1
                    logger.warning("max: rango %.2f-%.2f falló: %s", lo, hi, exc)
                    continue
                if total > RESULT_CAP and hi - lo > 0.02:
                    mid = round((lo + hi) / 2, 2)
                    pending[:0] = [(lo, mid), (round(mid + 0.01, 2), hi)]
                    continue
                if total > RESULT_CAP:
                    capped += total - RESULT_CAP
                ranges += 1
                declared_sum += total
                pages = -(-min(total, RESULT_CAP) // PER_PAGE)
                page = 1
                while True:
                    for item in results:
                        for rec in records_from_result(store, item, base_ids):
                            if rec.store_sku in seen:
                                continue
                            seen.add(rec.store_sku)
                            stats.records += 1
                            yield rec
                            if limit and stats.records >= limit:
                                cut = f" (limitada a {limit})"
                                return
                    page += 1
                    if len(results) < PER_PAGE or page > pages:
                        break
                    try:
                        _, results = await br.page(group, lo, hi, page)
                    except _BadResponse as exc:
                        stats.errors += 1
                        logger.warning("max: rango %.2f-%.2f pág %s falló: %s", lo, hi, page, exc)
                        break
    except StoreBlocked:
        stats.errors += 1
        cut = " (cortada por errores seguidos)"
    finally:
        note = (f"catálogo por rangos de precio del grupo raíz: {ranges} rangos, "
                f"{len(seen):,} SKUs únicos de {declared:,} declarados "
                f"({declared_sum:,} en los rangos recorridos)")
        if capped:
            note += f", {capped:,} fuera del tope de 10,000"
        stats.coverage_note = note + cut
