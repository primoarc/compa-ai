"""Frontend web de gt-compare.

Una sola página con barra de búsqueda que consulta las tiendas en paralelo
(reusa vtex.search_all) y devuelve los resultados ordenados por precio.

Levantar con:  gt-compare serve   (o: uvicorn gt_compare.web:app)
Requiere los extras web:  pip install -e ".[web]"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import html
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import planner, relevance, vtex
from .stores import load_stores

app = FastAPI(title="Compa AI", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"

TTL = 30 * 60
TIMEOUT = 8


MAX_ITEMS = 24  # tope de productos por tienda que devolvemos al front

SITE_URL = "https://gt-compare.vercel.app"


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
]

SEO_BY_SLUG = {page.slug: page for page in SEO_PAGES}


def _prod_dict(p) -> dict:
    return {
        "name": p.name,
        "price": p.price,
        "available": p.available > 0,
        "url": p.url,
        "image": p.image,
    }


def _best_per_store(query: str, results: list[vtex.StoreResult], plan=None) -> list[dict]:
    """Una fila por tienda con su producto más barato + todos los relevantes."""
    rows: list[dict] = []
    for res in results:
        rel = relevance.relevant_products(query, res.products, plan=plan) if res.ok else []
        rel.sort(key=lambda x: x.price)  # type: ignore[arg-type]
        if rel:
            items = [_prod_dict(p) for p in rel[:MAX_ITEMS]]
            rows.append({
                "store": res.store.name,
                "store_key": res.store.key,
                "ok": True,
                "count": len(rel),
                "items": items,
                **items[0],  # el más barato como cabecera de la fila
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
    rows.sort(key=lambda r: (not r["ok"], r.get("price") or float("inf")))
    return rows


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


@app.get("/api/search")
async def api_search(q: str, store: Optional[str] = None) -> JSONResponse:
    q = (q or "").strip()
    if not q:
        return JSONResponse({"query": q, "results": [], "cheapest": None})
    plan, rows, cheapest = await _search_rows(q, store=store)
    return JSONResponse({
        "query": q,
        "normalized_query": plan.canonical_query,
        "planner": plan.source,
        "results": rows,
        "cheapest": cheapest,
    })


@app.get("/api/stores")
async def api_stores() -> JSONResponse:
    return JSONResponse([
        {"key": s.key, "name": s.name} for s in load_stores()
    ])


@app.get("/comparar/{slug}", response_class=HTMLResponse)
async def seo_compare(slug: str) -> HTMLResponse:
    page = SEO_BY_SLUG.get(slug)
    if page is None:
        return HTMLResponse(_not_found_html(), status_code=404)
    plan, rows, cheapest = await _search_rows(page.query, use_openai_plan=False)
    return HTMLResponse(_seo_page_html(page, rows, cheapest, plan.source))


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
        f'El precio más bajo encontrado fue <strong>{_money(best.get("price"))}</strong> en <strong>{_e(best.get("store"))}</strong>.'
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
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
<style>
body{{margin:0;background:#0a0b12;color:#e8eaf2;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;line-height:1.5}}
a{{color:inherit}}.wrap{{max-width:980px;margin:0 auto;padding:28px 16px 64px}}
.top{{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:34px}}
.brand{{font-weight:900;font-size:22px;text-decoration:none;color:#fff}}.brand span{{color:#22d3ee}}
.search{{color:#cbbcff;text-decoration:none;border:1px solid #2b3150;padding:9px 13px;border-radius:10px}}
h1{{font-size:clamp(34px,6vw,62px);line-height:1.02;margin:0 0 14px;color:#fff;letter-spacing:0}}
.lead{{color:#a7acc4;font-size:18px;max-width:760px}}.summary{{margin:22px 0;padding:18px;border:1px solid #263052;background:#121524;border-radius:14px}}
.grid{{display:grid;gap:12px;margin-top:22px}}.card{{display:grid;grid-template-columns:44px 74px 1fr auto;gap:14px;align-items:center;background:#161827;border:1px solid #262a40;border-radius:16px;padding:14px;text-decoration:none}}
.card.best{{border-color:#1fd286;box-shadow:0 0 0 1px #1fd286}}.rank{{color:#8b8fa6;font-weight:800;text-align:center}}img{{width:74px;height:74px;object-fit:contain;background:#fff;border-radius:12px}}
.store{{color:#22d3ee;font-size:12px;font-weight:800;text-transform:uppercase}}.name{{font-size:16px;color:#fff}}.stock{{color:#8b8fa6;font-size:13px}}.price{{font-size:24px;font-weight:900;color:#fff;text-align:right}}.best .price{{color:#1fd286}}
.badge{{font-size:11px;background:#1fd286;color:#062412;font-weight:900;padding:4px 8px;border-radius:8px;margin-top:4px;display:inline-block}}
.section{{margin-top:36px}}.links{{display:flex;flex-wrap:wrap;gap:10px}}.links a{{text-decoration:none;color:#cbd5e1;border:1px solid #2b3150;border-radius:999px;padding:8px 12px;background:#11131f}}
ul{{color:#a7acc4}}footer{{margin-top:44px;color:#8b8fa6;font-size:13px}}@media(max-width:680px){{.top{{align-items:flex-start;flex-direction:column}}.card{{grid-template-columns:30px 58px 1fr}}img{{width:58px;height:58px}}.price{{grid-column:3;text-align:left;font-size:20px}}}}
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
  <footer>Compa AI compara precios públicos de tiendas en Guatemala. Los precios pueden cambiar al abrir la tienda.</footer>
</main>
</body>
</html>"""


def _seo_result_card(row: dict, idx: int, is_best: bool) -> str:
    image = row.get("image") or ""
    img = f'<img src="{_e(image)}" alt="{_e(row.get("name"))}" loading="lazy">' if image else '<div></div>'
    badge = '<span class="badge">MÁS BARATO</span>' if is_best else ""
    return f"""<a class="card {'best' if is_best else ''}" href="{_e(row.get("url"))}" rel="nofollow noopener" target="_blank">
  <div class="rank">{idx + 1}</div>
  {img}
  <div>
    <div class="store">{_e(row.get("store"))}</div>
    <div class="name">{_e(row.get("name"))}</div>
    <div class="stock">{'Disponible' if row.get('available') else 'Agotado'} · {int(row.get('count') or 1)} relevantes</div>
  </div>
  <div class="price">{_money(row.get("price"))}<br>{badge}</div>
</a>"""


def _not_found_html() -> str:
    links = "".join(f'<li><a href="/comparar/{_e(page.slug)}">{_e(page.h1)}</a></li>' for page in SEO_PAGES)
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>Página no encontrada | Compa AI</title></head>
<body><h1>Página no encontrada</h1><p>Comparaciones disponibles:</p><ul>{links}</ul></body></html>"""
