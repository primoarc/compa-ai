"""Frontend web de gt-compare.

Una sola página con barra de búsqueda que consulta las tiendas en paralelo
(reusa vtex.search_all) y devuelve los resultados ordenados por precio.

Levantar con:  gt-compare serve   (o: uvicorn gt_compare.web:app)
Requiere los extras web:  pip install -e ".[web]"
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from . import planner, relevance, vtex
from .stores import load_stores

app = FastAPI(title="Compa AI", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"

TTL = 30 * 60
TIMEOUT = 8


MAX_ITEMS = 24  # tope de productos por tienda que devolvemos al front


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


@app.get("/api/search")
async def api_search(q: str, store: Optional[str] = None) -> JSONResponse:
    q = (q or "").strip()
    if not q:
        return JSONResponse({"query": q, "results": [], "cheapest": None})
    stores = load_stores(only=store)
    plan = await planner.build_query_plan(q)
    results = await vtex.search_all(stores, q, timeout=TIMEOUT, ttl_seconds=TTL, plan=plan)
    rows = _best_per_store(q, results, plan=plan)
    cheapest = next((r["store_key"] for r in rows if r["ok"]), None)
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


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
