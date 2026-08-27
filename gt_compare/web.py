"""Frontend web de gt-compare.

Una sola página con barra de búsqueda que consulta las tiendas en paralelo
(reusa vtex.search_all) y devuelve los resultados ordenados por precio.

Levantar con:  gt-compare serve   (o: uvicorn gt_compare.web:app)
Requiere los extras web:  pip install -e ".[web]"
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import html
import json
import os
from pathlib import Path
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import matching, planner, relevance, vtex
from .stores import load_stores

app = FastAPI(title="Compa AI", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"

TTL = 30 * 60
TIMEOUT = 8


MAX_ITEMS = 24  # tope de productos por tienda que devolvemos al front
MAX_MODEL_GROUPS = 3  # grupos "mismo modelo, distinto precio" que destacamos

# Caché compartido en el CDN de Vercel. En serverless el caché en disco vive en
# /tmp: es efímero y por instancia, así que cada arranque en frío reconsultaba
# las 13 tiendas y Kemik nos empezó a devolver 429. La red de borde sí es
# compartida entre todos los usuarios e instancias, no hay que aprovisionar
# nada y evita la mayoría de las consultas al origen.
#   max-age=0        -> el navegador siempre revalida (el usuario ve lo actual)
#   s-maxage         -> el CDN sirve la misma respuesta a todo el mundo
#   stale-while-revalidate -> responde al instante mientras refresca por detrás
SEARCH_CACHE_CONTROL = "public, max-age=0, s-maxage=600, stale-while-revalidate=3600"
PAGE_CACHE_CONTROL = "public, max-age=0, s-maxage=1800, stale-while-revalidate=86400"
# Si alguna tienda se cayó por algo pasajero, no queremos que el CDN fije esa
# respuesta incompleta diez minutos: se reintenta pronto.
DEGRADED_CACHE_CONTROL = "public, max-age=0, s-maxage=60, stale-while-revalidate=300"

# Errores que indican una caída pasajera, no que la tienda no tenga el producto.
_TRANSIENT_MARKERS = ("429", "timeout", "500", "502", "503", "504", "connect")


def _is_degraded(rows: list[dict]) -> bool:
    for row in rows:
        if row.get("ok"):
            continue
        err = (row.get("error") or "").lower()
        if any(m in err for m in _TRANSIENT_MARKERS):
            return True
    return False

SITE_URL = "https://gt-compare.vercel.app"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_PER_MINUTE = int(os.getenv("GT_COMPARE_RATE_LIMIT_PER_MINUTE", "80"))
SEARCH_LOG_PATH = Path(
    os.getenv(
        "GT_COMPARE_SEARCH_LOG",
        "/tmp/gt-compare-searches.jsonl" if os.getenv("VERCEL") else str(Path.home() / ".gt-compare" / "searches.jsonl"),
    )
)
_RATE_BUCKETS: dict[str, list[float]] = {}


@dataclass(frozen=True)
class SeoPage:
    slug: str
    query: str
    title: str
    h1: str
    description: str


SEO_PAGES: list[SeoPage] = [
    SeoPage(
        "televisores-samsung-guatemala",
        "televisor samsung",
        "Precios de televisores Samsung en Guatemala | Compa AI",
        "Precios de televisores Samsung en Guatemala",
        "Compara precios de televisores Samsung en tiendas de Guatemala y encuentra la opción más barata disponible hoy.",
    ),
    SeoPage(
        "televisores-55-pulgadas-guatemala",
        "televisor 55",
        "Comparar precios de televisores 55 pulgadas en Guatemala | Compa AI",
        "Comparar precios de televisores 55 pulgadas",
        "Ranking de precios para televisores de 55 pulgadas en Guatemala, ordenado de menor a mayor precio.",
    ),
    SeoPage(
        "lavadoras-guatemala",
        "lavadora",
        "Comparar precios de lavadoras en Guatemala | Compa AI",
        "Comparar precios de lavadoras en Guatemala",
        "Consulta precios de lavadoras en tiendas guatemaltecas y compara disponibilidad en un solo lugar.",
    ),
    SeoPage(
        "lavadora-mabe-mas-barata-guatemala",
        "lavadora mabe",
        "Lavadora Mabe más barata en Guatemala | Compa AI",
        "Lavadora Mabe más barata en Guatemala",
        "Encuentra y compara precios de lavadoras Mabe disponibles en tiendas de Guatemala.",
    ),
    SeoPage(
        "taladro-dewalt-guatemala",
        "taladro dewalt",
        "Dónde comprar taladro DeWalt en Guatemala | Compa AI",
        "Dónde comprar taladro DeWalt en Guatemala",
        "Compara precios de taladros DeWalt en Guatemala y revisa qué tienda tiene mejor precio.",
    ),
    SeoPage(
        "taladros-guatemala",
        "taladro",
        "Comparar precios de taladros en Guatemala | Compa AI",
        "Comparar precios de taladros en Guatemala",
        "Precios de taladros en Guatemala ordenados por precio, tienda y disponibilidad.",
    ),
    SeoPage(
        "ps5-guatemala",
        "ps5",
        "Precios de PS5 en Guatemala | Compa AI",
        "Precios de PS5 en Guatemala",
        "Compara precios de PlayStation 5 y consolas PS5 en tiendas de Guatemala.",
    ),
    SeoPage(
        "cafeteras-guatemala",
        "cafetera",
        "Comparar precios de cafeteras en Guatemala | Compa AI",
        "Comparar precios de cafeteras en Guatemala",
        "Encuentra cafeteras, percoladoras y coffee makers al mejor precio en Guatemala.",
    ),
    SeoPage(
        "freidoras-de-aire-guatemala",
        "freidora de aire",
        "Comparar precios de freidoras de aire en Guatemala | Compa AI",
        "Comparar precios de freidoras de aire",
        "Consulta precios de air fryers y freidoras de aire en tiendas guatemaltecas.",
    ),
    SeoPage(
        "refrigeradoras-guatemala",
        "refrigeradora",
        "Comparar precios de refrigeradoras en Guatemala | Compa AI",
        "Comparar precios de refrigeradoras en Guatemala",
        "Compara refrigeradoras disponibles en Guatemala por precio y tienda.",
    ),
    SeoPage(
        "microondas-guatemala",
        "microondas",
        "Comparar precios de microondas en Guatemala | Compa AI",
        "Comparar precios de microondas en Guatemala",
        "Precios actualizados de hornos microondas en tiendas de Guatemala.",
    ),
    SeoPage(
        "laptops-guatemala",
        "laptop",
        "Comparar precios de laptops en Guatemala | Compa AI",
        "Comparar precios de laptops en Guatemala",
        "Compara laptops disponibles en Guatemala y encuentra las opciones más baratas.",
    ),
    SeoPage(
        "mouse-guatemala",
        "mouse",
        "Comparar precios de mouse en Guatemala | Compa AI",
        "Comparar precios de mouse en Guatemala",
        "Ranking de mouse alámbricos e inalámbricos disponibles en tiendas guatemaltecas.",
    ),
    SeoPage(
        "audifonos-guatemala",
        "audifonos",
        "Comparar precios de audífonos en Guatemala | Compa AI",
        "Comparar precios de audífonos en Guatemala",
        "Compara precios de audífonos, auriculares y earbuds en Guatemala.",
    ),
    SeoPage(
        "secadoras-de-pelo-guatemala",
        "secadora de pelo",
        "Comparar precios de secadoras de pelo en Guatemala | Compa AI",
        "Comparar precios de secadoras de pelo",
        "Encuentra secadoras de pelo y secadores de cabello al mejor precio en Guatemala.",
    ),
    SeoPage(
        "planchas-de-pelo-guatemala",
        "plancha de pelo",
        "Comparar precios de planchas de pelo en Guatemala | Compa AI",
        "Comparar precios de planchas de pelo",
        "Compara planchas de pelo, alisadoras y planchas para cabello en Guatemala.",
    ),
    SeoPage(
        "treats-para-perro-guatemala",
        "treats para perro",
        "Comparar precios de treats para perro en Guatemala | Compa AI",
        "Comparar precios de treats para perro",
        "Encuentra snacks, premios y treats para perros al mejor precio en Guatemala.",
    ),
    SeoPage(
        "comida-para-perro-guatemala",
        "comida para perro",
        "Comparar precios de comida para perro en Guatemala | Compa AI",
        "Comparar precios de comida para perro",
        "Compara alimento, concentrado y comida para perro disponible en Guatemala.",
    ),
    SeoPage(
        "owala-guatemala",
        "owala",
        "Precios de Owala en Guatemala | Compa AI",
        "Precios de Owala en Guatemala",
        "Compara botellas y vasos Owala disponibles en tiendas de Guatemala.",
    ),
]

SEO_BY_SLUG = {page.slug: page for page in SEO_PAGES}


def _prod_dict(p) -> dict:
    return {
        "name": p.name,
        "price": p.price,
        "available": p.available > 0,
        "url": p.url,
        "image": p.image,
        # Diagonal declarada en el título. El front la muestra como chip para
        # que se vea de una que dos filas no son el mismo aparato.
        "size": matching.screen_size(p.name),
    }


def _sort_key(p) -> tuple[bool, float]:
    """Primero disponible; después, menor precio."""
    return (not (getattr(p, "available", 0) > 0), float(getattr(p, "price", None) or float("inf")))


def _row_sort_key(row: dict) -> tuple[bool, bool, float]:
    return (not row["ok"], not bool(row.get("available")), float(row.get("price") or float("inf")))


def _best_per_store(query: str, results: list[vtex.StoreResult], plan=None) -> list[dict]:
    """Una fila por tienda con su producto más barato + todos los relevantes."""
    rows: list[dict] = []
    for res in results:
        relevant_all = [
            p for p in res.products
            if res.ok and relevance.is_relevant(query, p.name, plan=plan)
        ]
        priced = [
            p for p in relevant_all
            if getattr(p, "price", None) and p.price > 0
        ]
        priced.sort(key=_sort_key)
        if priced:
            items = [_prod_dict(p) for p in priced[:MAX_ITEMS]]
            rows.append({
                "store": res.store.name,
                "store_key": res.store.key,
                "ok": True,
                "count": len(priced),
                "items": items,
                # Si la tienda no respondió y estamos sirviendo el último
                # snapshot, el front lo etiqueta. Un precio guardado presentado
                # como si fuera en vivo sería peor que no mostrarlo.
                "stale_age": res.stale_age,
                **items[0],  # el más barato como cabecera de la fila
            })
        elif relevant_all:
            rows.append({
                "store": res.store.name,
                "store_key": res.store.key,
                "ok": False,
                "error": "productos encontrados sin precio público",
                "found_without_price": True,
                "count": len(relevant_all),
                "items": [_prod_dict(p) for p in relevant_all[:MAX_ITEMS]],
            })
        else:
            # distinguir "tienda falló" de "sin coincidencia relevante"
            if not res.ok:
                err = res.error or "sin resultados"
            elif any(p.price and p.price > 0 for p in res.products):
                err = "sin coincidencia para tu búsqueda"
            else:
                err = "sin resultados"
            rows.append({
                "store": res.store.name,
                "store_key": res.store.key,
                "ok": False,
                "error": err,
            })
    rows.sort(key=_row_sort_key)
    return rows


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    if RATE_LIMIT_PER_MINUTE <= 0:
        return
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    key = _client_key(request)
    hits = [ts for ts in _RATE_BUCKETS.get(key, []) if ts >= cutoff]
    if len(hits) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Demasiadas búsquedas. Probá de nuevo en un minuto.")
    hits.append(now)
    _RATE_BUCKETS[key] = hits


def _log_search(q: str, store: str | None, rows: list[dict], cheapest: str | None) -> None:
    try:
        SEARCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": int(time.time()),
            "query": q,
            "store": store,
            "stores_with_price": sum(1 for row in rows if row.get("ok")),
            "cheapest": cheapest,
        }
        with SEARCH_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


async def _search_rows(
    q: str,
    store: Optional[str] = None,
    *,
    use_openai_plan: bool = True,
) -> tuple[planner.QueryPlan, list[dict], str | None]:
    stores = load_stores(only=store)
    plan = await planner.build_query_plan(q) if use_openai_plan else planner.local_plan(q)
    results = await vtex.search_all(stores, q, timeout=TIMEOUT, ttl_seconds=TTL, plan=plan)
    rows = _best_per_store(q, results, plan=plan)
    cheapest = next((r["store_key"] for r in rows if r["ok"]), None)
    return plan, rows, cheapest


def _model_groups(rows: list[dict]) -> list[dict]:
    """Ofertas del MISMO modelo en tiendas distintas.

    Es la comparación que de verdad justifica el sitio: el mismo televisor a
    Q1,397 en una tienda y a Q2,399 en otra. Se arma sobre todas las ofertas,
    no solo sobre la más barata de cada tienda, porque el modelo compartido
    puede no ser el producto más barato de esa tienda.
    """
    offers: list[dict] = []
    for row in rows:
        for item in row.get("items") or []:
            if item.get("price"):
                offers.append({
                    **item,
                    "store": row.get("store"),
                    "store_key": row.get("store_key"),
                })
    return matching.group_offers(offers)[:MAX_MODEL_GROUPS]


@app.get("/api/search")
async def api_search(request: Request, q: str, store: Optional[str] = None) -> JSONResponse:
    _check_rate_limit(request)
    q = (q or "").strip()
    if not q:
        return JSONResponse({"query": q, "results": [], "cheapest": None})
    plan, rows, cheapest = await _search_rows(q, store=store)
    _log_search(q, store, rows, cheapest)
    return JSONResponse(
        {
            "query": q,
            "normalized_query": plan.canonical_query,
            "planner": plan.source,
            "results": rows,
            "cheapest": cheapest,
            "model_groups": _model_groups(rows),
            # Momento real de la consulta a las tiendas. Importa porque la
            # respuesta puede venir del caché de borde y tener varios minutos:
            # el front muestra la antigüedad en vez de dar a entender que todo
            # se acaba de consultar.
            "generated_at": int(time.time()),
        },
        headers={
            "Cache-Control": (
                DEGRADED_CACHE_CONTROL if _is_degraded(rows) else SEARCH_CACHE_CONTROL
            )
        },
    )


@app.get("/api/stores")
async def api_stores() -> JSONResponse:
    return JSONResponse([
        {"key": s.key, "name": s.name} for s in load_stores()
    ])


@app.get("/api/popular")
async def api_popular(limit: int = 12) -> JSONResponse:
    counter: Counter[str] = Counter()
    try:
        with SEARCH_LOG_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                query = " ".join(str(item.get("query") or "").lower().split())
                if query:
                    counter[query] += 1
    except OSError:
        return JSONResponse([])

    capped = max(1, min(limit, 50))
    return JSONResponse([
        {"query": query, "count": count}
        for query, count in counter.most_common(capped)
    ])


@app.get("/comparar/{slug}", response_class=HTMLResponse)
async def seo_compare(slug: str) -> HTMLResponse:
    page = SEO_BY_SLUG.get(slug)
    if page is None:
        return HTMLResponse(_not_found_html(), status_code=404)
    plan, rows, cheapest = await _search_rows(page.query, use_openai_plan=False)
    return HTMLResponse(
        _seo_page_html(page, rows, cheapest, plan.source),
        headers={"Cache-Control": PAGE_CACHE_CONTROL},
    )


@app.get("/sitemap.xml")
async def sitemap() -> Response:
    today = date.today().isoformat()
    urls = [
        f"""  <url><loc>{SITE_URL}/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>"""
    ]
    urls.extend(
        f"""  <url><loc>{SITE_URL}/comparar/{page.slug}</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>"""
        for page in SEO_PAGES
    )
    body = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        *urls,
        "</urlset>",
    ])
    return Response(body, media_type="application/xml")


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    return "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {SITE_URL}/sitemap.xml",
        "",
    ])


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt() -> str:
    links = "\n".join(
        f"- [{page.h1}]({SITE_URL}/comparar/{page.slug}): {page.description}"
        for page in SEO_PAGES
    )
    return f"""# Compa AI

Compa AI compara precios de productos en tiendas de Guatemala y publica paginas indexables con resultados pre-renderizados.

## Paginas principales

- [Inicio]({SITE_URL}/): Comparador de precios en tiendas de Guatemala.
{links}
"""


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


def _money(value: float | int | None) -> str:
    if value is None:
        return "N/D"
    return f"Q{float(value):,.2f}"


def _e(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _seo_page_html(page: SeoPage, rows: list[dict], cheapest: str | None, plan_source: str) -> str:
    ok = [r for r in rows if r.get("ok")]
    fail = [r for r in rows if not r.get("ok")]
    canonical = f"{SITE_URL}/comparar/{page.slug}"
    best = ok[0] if ok else None
    updated = date.today().isoformat()
    item_list = [
        {
            "@type": "ListItem",
            "position": idx + 1,
            "name": row.get("name"),
            "url": row.get("url"),
            "item": {
                "@type": "Product",
                "name": row.get("name"),
                "image": row.get("image"),
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "GTQ",
                    "price": row.get("price"),
                    "availability": "https://schema.org/InStock" if row.get("available") else "https://schema.org/OutOfStock",
                    "seller": {"@type": "Organization", "name": row.get("store")},
                    "url": row.get("url"),
                },
            },
        }
        for idx, row in enumerate(ok[:10])
    ]
    schema = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": page.h1,
        "description": page.description,
        "url": canonical,
        "dateModified": updated,
        "itemListElement": item_list,
    }
    related = "".join(
        f'<a href="/comparar/{_e(p.slug)}">{_e(p.h1)}</a>'
        for p in SEO_PAGES
        if p.slug != page.slug
    )
    rows_html = "".join(_seo_result_card(row, idx, row.get("store_key") == cheapest) for idx, row in enumerate(ok))
    failures = "".join(
        f'<li><strong>{_e(row.get("store"))}</strong>: {_e(row.get("error"))}</li>'
        for row in fail
    )
    best_copy = (
        f'El precio más bajo con precio público fue <strong>{_money(best.get("price"))}</strong> en <strong>{_e(best.get("store"))}</strong>.'
        if best else
        "No encontramos productos relevantes con precio en las tiendas consultadas."
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(page.title)}</title>
<meta name="description" content="{_e(page.description)}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{_e(page.title)}">
<meta property="og:description" content="{_e(page.description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:type" content="website">
<meta name="theme-color" content="#ffffff">
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400;0,500;0,600;1,400&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{{--paper:#fdfdfc;--white:#fff;--ink:#0b0b0c;--ink-2:#3a3a3e;--muted:#76767c;--faint:#a1a1a8;
--rule:rgba(11,11,12,.09);--rule-2:rgba(11,11,12,.055);--wash:#f6f6f4;--emerald:#0a6b47;--emerald-soft:#e9f4ef;--amber:#8a5a00;
--serif:"Instrument Serif","Iowan Old Style",Georgia,serif;--sans:"Instrument Sans","Helvetica Neue",Helvetica,Arial,sans-serif}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.5;
-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}}
a{{color:inherit}}::selection{{background:var(--emerald);color:#fff}}
.wrap{{max-width:880px;margin:0 auto;padding:22px 24px 72px}}
.top{{display:flex;justify-content:space-between;gap:16px;align-items:center;
padding-bottom:20px;margin-bottom:44px;border-bottom:1px solid var(--rule)}}
.brand{{font-family:var(--serif);font-size:23px;letter-spacing:-.01em;text-decoration:none}}
.brand span{{font-style:italic;color:var(--emerald)}}
.search{{font-size:13.5px;font-weight:500;color:var(--ink);text-decoration:none;
border:1px solid var(--rule);border-radius:999px;padding:8px 16px;transition:background .18s,border-color .18s}}
.search:hover{{background:var(--wash);border-color:rgba(11,11,12,.2)}}
h1{{font-family:var(--serif);font-weight:400;font-size:clamp(34px,6vw,58px);line-height:1.03;
margin:0 0 16px;letter-spacing:-.02em;text-wrap:balance}}
.lead{{color:var(--muted);font-size:17px;max-width:620px;margin:0}}
.summary{{margin:28px 0 0;padding:18px 20px;border:1px solid var(--rule-2);background:var(--white);border-radius:14px}}
.summary p{{margin:0;font-size:14.5px;color:var(--muted)}}
.summary p+p{{margin-top:8px;font-size:13px;color:var(--faint)}}
.summary strong{{color:var(--emerald);font-weight:600;font-variant-numeric:tabular-nums}}
.grid{{display:flex;flex-direction:column;margin-top:34px}}
.card{{display:grid;grid-template-columns:26px 56px 1fr auto;gap:16px;align-items:center;
padding:16px 14px;margin:0 -14px;border-radius:14px;border-bottom:1px solid var(--rule-2);
text-decoration:none;position:relative;transition:background .18s}}
.card:hover{{background:var(--white);box-shadow:0 1px 2px rgba(11,11,12,.04),0 10px 30px -22px rgba(11,11,12,.4)}}
.card.best{{background:var(--emerald-soft)}}
.card.best::before{{content:"";position:absolute;left:0;top:12px;bottom:12px;width:2px;border-radius:2px;background:var(--emerald)}}
.rank{{color:var(--faint);text-align:center;font-size:12.5px;font-variant-numeric:tabular-nums}}
.best .rank{{color:var(--emerald);font-weight:600}}
img{{width:56px;height:56px;object-fit:contain;background:var(--white);border:1px solid var(--rule-2);border-radius:9px}}
.store{{color:var(--muted);font-size:10.5px;font-weight:600;letter-spacing:.11em;text-transform:uppercase}}
.best .store{{color:var(--emerald)}}
.name{{font-size:14.5px;color:var(--ink);margin-top:3px;line-height:1.35}}
.stock{{color:var(--faint);font-size:11.5px;margin-top:5px}}
.price{{font-size:19px;font-weight:600;text-align:right;letter-spacing:-.02em;font-variant-numeric:tabular-nums;white-space:nowrap}}
.best .price{{color:var(--emerald)}}
.badge{{font-size:9.5px;color:var(--emerald);font-weight:600;letter-spacing:.1em;text-transform:uppercase;margin-top:4px;display:inline-block}}
.section{{margin-top:48px}}
.section h2{{font-size:10.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin:0 0 14px}}
.links{{display:flex;flex-wrap:wrap;gap:7px}}
.links a{{text-decoration:none;font-size:13px;color:var(--ink-2);border:1px solid var(--rule-2);
border-radius:999px;padding:7px 13px;transition:background .18s,border-color .18s}}
.links a:hover{{background:var(--wash);border-color:var(--rule)}}
ul{{color:var(--muted);font-size:13.5px;margin:0;padding-left:18px}}
ul li{{padding:3px 0}}ul strong{{color:var(--ink-2);font-weight:600}}
footer{{margin-top:56px;padding-top:24px;border-top:1px solid var(--rule);color:var(--faint);font-size:12.5px}}
@media(max-width:680px){{
.wrap{{padding:18px 18px 64px}}
.top{{align-items:flex-start;flex-direction:column;gap:12px}}
.card{{grid-template-columns:20px 46px 1fr;padding:14px 10px;margin:0 -10px;gap:12px}}
img{{width:46px;height:46px}}
.price{{grid-column:3;text-align:left;font-size:17px;margin-top:6px}}}}
</style>
</head>
<body>
<main class="wrap">
  <nav class="top"><a class="brand" href="/">Compa <span>AI</span></a><a class="search" href="/?q={_e(page.query)}">Buscar en vivo</a></nav>
  <h1>{_e(page.h1)}</h1>
  <p class="lead">{_e(page.description)}</p>
  <div class="summary">
    <p>{best_copy}</p>
    <p>Consulta actualizada: <time datetime="{updated}">{updated}</time>. Tiendas comparadas: {len(rows)}. Motor de normalización: {_e(plan_source)}.</p>
  </div>
  <section class="grid" aria-label="Resultados de precios">
    {rows_html or '<p>No hay resultados relevantes por ahora.</p>'}
  </section>
  <section class="section">
    <h2>Tiendas sin resultado relevante</h2>
    <ul>{failures or '<li>Todas las tiendas consultadas devolvieron al menos un resultado relevante.</li>'}</ul>
  </section>
  <section class="section">
    <h2>Más comparaciones populares</h2>
    <div class="links">{related}</div>
  </section>
  <footer>Compa AI compara precios públicos de tiendas en Guatemala. Los precios pueden cambiar al abrir la tienda. En PriceSmart el precio y disponibilidad pueden variar por club.</footer>
</main>
</body>
</html>"""


def _seo_result_card(row: dict, idx: int, is_best: bool) -> str:
    image = row.get("image") or ""
    img = f'<img src="{_e(image)}" alt="{_e(row.get("name"))}" loading="lazy">' if image else '<div></div>'
    badge = '<span class="badge">MÁS BARATO</span>' if is_best else ""
    price_note = " · puede variar por club" if row.get("store_key") == "pricesmart" else ""
    return f"""<a class="card {'best' if is_best else ''}" href="{_e(row.get("url"))}" rel="nofollow noopener" target="_blank">
  <div class="rank">{idx + 1}</div>
  {img}
  <div>
    <div class="store">{_e(row.get("store"))}</div>
    <div class="name">{_e(row.get("name"))}</div>
    <div class="stock">{'Disponible' if row.get('available') else 'Agotado'} · {int(row.get('count') or 1)} relevantes{price_note}</div>
  </div>
  <div class="price">{_money(row.get("price"))}<br>{badge}</div>
</a>"""


def _not_found_html() -> str:
    links = "".join(
        f'<a href="/comparar/{_e(page.slug)}">{_e(page.h1)}</a>' for page in SEO_PAGES
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Página no encontrada | Compa AI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{{--paper:#fdfdfc;--ink:#0b0b0c;--ink-2:#3a3a3e;--muted:#76767c;--faint:#a1a1a8;
--rule:rgba(11,11,12,.09);--rule-2:rgba(11,11,12,.055);--wash:#f6f6f4;--emerald:#0a6b47;
--serif:"Instrument Serif",Georgia,serif;--sans:"Instrument Sans","Helvetica Neue",Arial,sans-serif}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.5;
-webkit-font-smoothing:antialiased}}
a{{color:inherit}}
.wrap{{max-width:640px;margin:0 auto;padding:96px 24px 72px;text-align:center}}
.brand{{font-family:var(--serif);font-size:23px;text-decoration:none;display:inline-block;margin-bottom:56px}}
.brand span{{font-style:italic;color:var(--emerald)}}
h1{{font-family:var(--serif);font-weight:400;font-size:clamp(34px,7vw,52px);line-height:1.05;
letter-spacing:-.02em;margin:0 0 12px}}
p{{color:var(--muted);font-size:16px;margin:0 auto;max-width:400px}}
.home{{display:inline-block;margin-top:28px;background:var(--ink);color:#fff;text-decoration:none;
font-size:14.5px;font-weight:600;padding:13px 26px;border-radius:999px}}
.section{{margin-top:64px;padding-top:26px;border-top:1px solid var(--rule)}}
.section h2{{font-size:10.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;
color:var(--faint);margin:0 0 14px}}
.links{{display:flex;flex-wrap:wrap;gap:7px;justify-content:center}}
.links a{{text-decoration:none;font-size:13px;color:var(--ink-2);border:1px solid var(--rule-2);
border-radius:999px;padding:7px 13px;transition:background .18s,border-color .18s}}
.links a:hover{{background:var(--wash);border-color:var(--rule)}}
</style>
</head>
<body>
<main class="wrap">
  <a class="brand" href="/">Compa <span>AI</span></a>
  <h1>Esta página no existe</h1>
  <p>El enlace que seguiste no corresponde a ninguna comparación disponible.</p>
  <a class="home" href="/">Buscar un producto</a>
  <section class="section">
    <h2>Comparaciones disponibles</h2>
    <div class="links">{links}</div>
  </section>
</main>
</body>
</html>"""
