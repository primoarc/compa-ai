"""Páginas con historial: ficha de producto, ofertas, oferta del día, imágenes
para compartir, opt-in de alertas y panel de aprobación.

Todo se dibuja en el servidor con CSS en línea y fuentes del sistema: nada
bloquea el primer render. Sin base de historial (Vercel sin configurar) las
páginas responden 404 en vez de fallar.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import time
from datetime import date, timedelta
from typing import Optional
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import clusters
from .db import Database, get_db
from .decide import schemas as dschemas, taxonomy
from .history import series, today_utc, window_stats
from .images import thumb
from .stores import load_stores

router = APIRouter()

SITE_URL = "https://gt-compare.vercel.app"
PAGE_CACHE = "public, max-age=0, s-maxage=1800, stale-while-revalidate=86400"
OG_CACHE = "public, max-age=3600, s-maxage=86400"
FEED_KINDS = ("oferta", "oferta_fuerte", "posible_error")
FEED_STATUSES = ("published", "approved")
BADGE_MIN_DAYS = 14
BADGE_GOOD_MARGIN = 0.05
ALERT_CONSENT = ("Acepto que Compa AI me escriba por WhatsApp cuando este producto baje "
                 "al precio que indiqué. Puedo darme de baja respondiendo BAJA.")

_STORE_NAMES: dict[str, str] = {}


def store_name(key: str) -> str:
    if not _STORE_NAMES:
        _STORE_NAMES.update({s.key: s.name for s in load_stores()})
    return _STORE_NAMES.get(key, key)


def _db() -> Database:
    db = get_db()
    if db is None:
        raise HTTPException(404, "historial no disponible")
    return db


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def money(v: Optional[float]) -> str:
    return f"Q{float(v):,.2f}" if v is not None else "N/D"


# --- señales de precio ------------------------------------------------------
def price_badge(intervals: list[dict], price: float, today: str) -> Optional[tuple[str, str]]:
    """(texto, clase) o None si no hay historial suficiente para opinar."""
    s90 = window_stats(intervals, today, 90, exclude_today=True)
    if s90.days_covered < BADGE_MIN_DAYS or s90.p20 is None:
        return None
    s30 = window_stats(intervals, today, 30, exclude_today=True)
    # En lo más bajo de su rango y bastante debajo de lo normal (con precio
    # plano p20 == mediana y cualquier centavo menos contaría como "buen precio").
    if price <= s90.p20 + 0.005 and s90.median and price <= s90.median * (1 - BADGE_GOOD_MARGIN):
        return "Buen precio", "good"
    if s30.minimum and price >= s30.minimum * 1.10:
        return "Esperá, ha estado más barato", "wait"
    return "Precio normal", "normal"


def outbound(url: str) -> str:
    """Enlace a la tienda con UTM, para que su analítica vea el tráfico que le mandamos."""
    if not url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source=compa-ai&utm_medium=referral"


def whatsapp_url(name: str, price: float, store: str, url: str,
                 reference: Optional[float] = None, pct: Optional[int] = None,
                 vs_stores: bool = False) -> str:
    if reference and pct:
        against = "que en otras tiendas" if vs_stores else "que su precio normal"
        text = (f"{name} a {money(price)} en {store}, {pct}% menos {against} "
                f"({money(reference)}). Lo vi en Compa AI: {url}")
    else:
        text = f"{name} a {money(price)} en {store}. Compará precios en Compa AI: {url}"
    return "https://wa.me/?text=" + quote(text)


def chart_svg(intervals: list[dict], today: str, days: int = 90) -> str:
    """Escalera de precio (solo días disponibles) con la mediana de 30 días punteada.

    La ventana arranca en el primer dato si hay menos de `days` de historial,
    para no dejar dos tercios de la gráfica vacíos.
    """
    firsts = [r["start_day"] for r in intervals if r.get("price") is not None and r.get("available")]
    if not firsts:
        return ""
    days = max(7, min(days, (date.fromisoformat(today) - date.fromisoformat(min(firsts))).days + 1))
    start = date.fromisoformat(today) - timedelta(days=days - 1)
    pts: list[tuple[int, float]] = []
    for row in intervals:
        if row.get("price") is None or not row.get("available"):
            continue
        a = max(date.fromisoformat(row["start_day"]), start)
        b = min(date.fromisoformat(row["end_day"]), date.fromisoformat(today))
        if b < a:
            continue
        pts.append(((a - start).days, float(row["price"])))
        pts.append(((b - start).days + 1, float(row["price"])))
    if len(pts) < 2:
        return ""
    w, h, pl, pr, pt, pb = 640, 220, 8, 64, 16, 28
    lo, hi = min(p for _, p in pts), max(p for _, p in pts)
    span = (hi - lo) or max(hi * 0.1, 1.0)
    lo_axis, hi_axis = lo - span * 0.15, hi + span * 0.15

    def x(d: float) -> float:
        return pl + d / days * (w - pl - pr)

    def y(v: float) -> float:
        return pt + (hi_axis - v) / (hi_axis - lo_axis) * (h - pt - pb)

    path = []
    prev_end = None
    for i in range(0, len(pts), 2):
        (d0, v), (d1, _) = pts[i], pts[i + 1]
        cmd = "L" if prev_end is not None and d0 <= prev_end + 1 else "M"
        if cmd == "L":
            path.append(f"L{x(d0):.1f},{y(v):.1f}")
        else:
            path.append(f"M{x(d0):.1f},{y(v):.1f}")
        path.append(f"L{x(d1):.1f},{y(v):.1f}")
        prev_end = d1
    s30 = window_stats(intervals, today, 30, exclude_today=True)
    median_line = ""
    if s30.median:
        my = y(s30.median)
        median_line = f'<line x1="{pl}" x2="{w - pr}" y1="{my:.1f}" y2="{my:.1f}" class="med"/>'
        if min(abs(my - y(hi)), abs(my - y(lo))) > 14:  # sin pisar las etiquetas de máx y mín
            median_line += f'<text x="{w - pr + 6}" y="{my + 4:.1f}" class="lbl">mediana</text>'
    last_x, last_y = x(pts[-1][0]), y(pts[-1][1])
    labels = f'<text x="{w - pr + 6}" y="{y(hi) + 4:.1f}" class="lbl">{_e(money(hi))}</text>'
    if hi - lo > 0.005:
        labels += f'<text x="{w - pr + 6}" y="{y(lo) + 4:.1f}" class="lbl">{_e(money(lo))}</text>'
    axis = (f'<text x="{pl}" y="{h - 6}" class="lbl">hace {days} días</text>'
            f'<text x="{w - pr}" y="{h - 6}" class="lbl" text-anchor="end">hoy</text>')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="Precio de los últimos {days} días, entre {_e(money(lo))} y {_e(money(hi))}">'
            f'<line x1="{pl}" x2="{w - pr}" y1="{h - pb}" y2="{h - pb}" class="axis"/>'
            f'{median_line}<path d="{" ".join(path)}" class="line"/>'
            f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4.5" class="dot"/>{labels}{axis}</svg>')


# --- layout -----------------------------------------------------------------
CSS = """
:root{--bg:#fff;--ink:#0b0b0c;--ink2:#3a3a3e;--muted:#6e6e76;--faint:#9a9aa2;--rule:#e6e6e9;
--wash:#f4f4f5;--green:#0a6b47;--green-soft:#e9f4ef;--amber:#8a5a00;--amber-soft:#fbf3e2;--red:#a1261b;
--font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);line-height:1.45;-webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{max-width:760px;margin:0 auto;padding:14px 16px 64px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:6px 0 14px;border-bottom:1px solid var(--rule);margin-bottom:20px}
.brand{font-weight:700;font-size:18px;text-decoration:none;letter-spacing:-.01em}
.brand b{color:var(--green)}
.nav{display:flex;gap:16px;font-size:14px}.nav a{text-decoration:none;color:var(--ink2)}.nav a[aria-current]{color:var(--green);font-weight:600}
h1{font-size:clamp(22px,5vw,30px);line-height:1.2;margin:0 0 8px;letter-spacing:-.01em;font-weight:700}
h2{font-size:17px;margin:32px 0 10px;font-weight:650}
.muted{color:var(--muted)}.small{font-size:13px}
.price{font-size:clamp(30px,8vw,40px);font-weight:750;letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--green)}
.was{color:var(--muted);font-size:15px}.was s{color:var(--faint)}
.cond{font-size:14px;color:var(--ink2);margin-top:2px}.cond strong{color:var(--green)}
.badge{display:inline-block;font-size:13px;font-weight:650;padding:4px 10px;border-radius:6px}
.badge.good{background:var(--green-soft);color:var(--green)}
.badge.normal{background:var(--wash);color:var(--ink2)}
.badge.wait{background:var(--amber-soft);color:var(--amber)}
.hero{display:grid;grid-template-columns:120px 1fr;gap:16px;align-items:start}
.hero img{width:120px;height:120px;object-fit:contain;border:1px solid var(--rule);border-radius:10px;background:#fff}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 16px;border-radius:8px;
border:1px solid var(--ink);background:var(--ink);color:#fff;font-weight:600;font-size:15px;text-decoration:none;cursor:pointer;font-family:inherit}
.btn.ghost{background:#fff;color:var(--ink);border-color:var(--rule)}
.btn.green{background:var(--green);border-color:var(--green)}
.chart{width:100%;height:auto;margin-top:6px}
.chart .line{fill:none;stroke:var(--ink);stroke-width:2.2;stroke-linejoin:round}
.chart .med{stroke:var(--faint);stroke-dasharray:4 4}.chart .axis{stroke:var(--rule)}
.chart .dot{fill:var(--green)}.chart .lbl{font-size:11px;fill:var(--muted);font-family:var(--font)}
.rows{border-top:1px solid var(--rule)}
.row{display:grid;grid-template-columns:56px 1fr auto;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid var(--rule);text-decoration:none}
.row img{width:56px;height:56px;object-fit:contain;border:1px solid var(--rule);border-radius:8px;background:#fff}
.row .n{font-size:15px;line-height:1.3}.row .s{font-size:13px;color:var(--muted);margin-top:2px}
.row .p{text-align:right;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.row .d{display:block;font-size:13px;font-weight:650;color:var(--green)}
.row .d.err{color:var(--red)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 6px}
.filters select{min-height:44px;border:1px solid var(--rule);border-radius:8px;padding:0 10px;font:inherit;background:#fff;color:var(--ink)}
form.alert{display:grid;gap:10px;margin-top:10px;max-width:420px}
form.alert input{min-height:44px;border:1px solid var(--rule);border-radius:8px;padding:0 12px;font:inherit;width:100%}
form.alert label{display:grid;gap:4px;font-size:14px;color:var(--ink2)}
form.alert label.consent{display:flex;gap:10px;align-items:flex-start}
form.alert label.consent input{width:20px;min-height:20px;margin:2px 0 0;flex:none}
.note{font-size:13px;color:var(--muted);margin-top:28px;border-top:1px solid var(--rule);padding-top:14px}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{border-bottom:1px solid var(--rule);padding:8px 6px;text-align:left;vertical-align:top}
@media(max-width:520px){.hero{grid-template-columns:84px 1fr}.hero img{width:84px;height:84px}.nav{gap:12px;font-size:13px}}
"""


def layout(title: str, body: str, *, description: str = "", canonical: str = "",
           og_image: str = "", current: str = "", jsonld: Optional[dict] = None,
           noindex: bool = False) -> str:
    nav = "".join(
        f'<a href="{href}"{" aria-current=page" if current == key else ""}>{label}</a>'
        for key, href, label in (("buscar", "/", "Buscar"), ("ofertas", "/ofertas", "Ofertas"),
                                 ("dia", "/oferta-del-dia", "Oferta del día"))
    )
    og = ""
    if og_image:
        og = (f'<meta property="og:image" content="{_e(og_image)}"><meta property="og:image:width" content="1200">'
              f'<meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image">'
              f'<meta name="twitter:image" content="{_e(og_image)}">')
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>' if jsonld else ""
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><meta name="description" content="{_e(description)}">
{f'<link rel="canonical" href="{_e(canonical)}">' if canonical else ''}
{'<meta name="robots" content="noindex">' if noindex else ''}
<meta property="og:title" content="{_e(title)}"><meta property="og:description" content="{_e(description)}">
<meta property="og:type" content="website"><meta property="og:locale" content="es_GT">
{f'<meta property="og:url" content="{_e(canonical)}">' if canonical else ''}{og}
<link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta name="theme-color" content="#ffffff">
<style>{CSS}</style>{ld}</head>
<body><main class="wrap"><nav class="top"><a class="brand" href="/">Compa <b>AI</b></a><div class="nav">{nav}</div></nav>
{body}
<p class="note">Precios públicos de cada tienda, revisados una vez al día. Pueden cambiar al abrir la tienda.</p>
</main><script defer src="/_vercel/insights/script.js"></script></body></html>"""


# --- consultas ----------------------------------------------------------------
def _product(db: Database, pid: int) -> dict:
    row = db.query_one("SELECT * FROM products WHERE id=?", (pid,))
    if row is None:
        raise HTTPException(404, "producto no encontrado")
    return row


def _latest_deal(db: Database, pid: int) -> Optional[dict]:
    marks = ",".join("?" * len(FEED_STATUSES))
    return db.query_one(
        f"""SELECT * FROM deals WHERE product_id=? AND status IN ({marks})
            AND detected_on >= date('now','-2 day') ORDER BY detected_on DESC LIMIT 1""",
        (pid, *FEED_STATUSES),
    )


def _feed_day(db: Database) -> Optional[str]:
    row = db.query_one("SELECT MAX(detected_on) AS d FROM deals")
    return row["d"] if row else None


def feed(db: Database, *, category: str = "", store: str = "", limit: int = 60) -> list[dict]:
    day = _feed_day(db)
    if not day:
        return []
    sql = [f"""SELECT d.*, p.name, p.store_key, p.url, p.image, p.category_id, p.cur_price
               FROM deals d JOIN products p ON p.id = d.product_id
               WHERE d.detected_on = ? AND d.kind IN ({",".join("?" * len(FEED_KINDS))})
               AND d.status IN ({",".join("?" * len(FEED_STATUSES))})"""]
    args: list = [day, *FEED_KINDS, *FEED_STATUSES]
    # Un price error solo sale si alguien lo aprobó en el panel.
    sql.append("AND (d.kind != 'posible_error' OR d.status = 'approved')")
    if category:
        sql.append("AND (p.category_id = ? OR p.category_id LIKE ?)")
        args += [category, category + "/%"]
    if store:
        sql.append("AND p.store_key = ?")
        args.append(store)
    sql.append("ORDER BY d.score DESC LIMIT ?")
    args.append(limit)
    return db.query(" ".join(sql), args)


def _vs_stores(deal: Optional[dict]) -> bool:
    """¿La referencia del deal es el precio en otras tiendas (y no su propio historial)?"""
    if not deal:
        return False
    try:
        return json.loads(deal.get("features") or "{}").get("reference_kind") == "otras_tiendas"
    except ValueError:
        return False


def _pct(price: float, reference: Optional[float]) -> Optional[int]:
    if not reference or reference <= price:
        return None
    return round((1 - price / reference) * 100)


def _deal_row(d: dict) -> str:
    pct = _pct(d["price"], d["reference"])
    img = (f'<img src="{_e(thumb(d["image"], 112))}" alt="" loading="lazy" width="56" height="56">'
           if d.get("image") else "<span></span>")
    vs_stores = _vs_stores(d)
    if d["kind"] == "posible_error":
        tag = "Posible error de precio"
    elif pct:
        tag = f"{pct}% menos que otras tiendas" if vs_stores else f"{pct}% bajo lo normal"
    else:
        tag = ""
    ref_label = "en otras tiendas" if vs_stores else "normal"
    return (f'<a class="row" href="/p/{d["product_id"]}">{img}<div><div class="n">{_e(d["name"])}</div>'
            f'<div class="s">{_e(store_name(d["store_key"]))} · {ref_label} {_e(money(d["reference"]))}</div></div>'
            f'<div class="p">{_e(money(d["price"]))}<span class="d{" err" if d["kind"] == "posible_error" else ""}">{_e(tag)}</span></div></a>')


# --- rutas públicas ---------------------------------------------------------
@router.get("/p/{pid}", response_class=HTMLResponse)
async def product_page(pid: int) -> HTMLResponse:
    db = _db()
    p = _product(db, pid)
    today = today_utc()
    hist = series(db, pid, (date.fromisoformat(today) - timedelta(days=95)).isoformat())
    price = float(p["cur_price"] or 0)
    badge = price_badge(hist, price, today)
    deal = _latest_deal(db, pid)
    s30 = window_stats(hist, today, 30, exclude_today=True)
    reference = deal["reference"] if deal else s30.median
    pct = _pct(price, reference)
    store = store_name(p["store_key"])
    canonical = f"{SITE_URL}/p/{pid}"
    others = clusters.peers(db, pid)
    rows = "".join(
        f'<a class="row" href="/p/{o["id"]}"><span></span><div><div class="n">{_e(store_name(o["store_key"]))}</div>'
        f'<div class="s">{_e(o["name"])}</div></div><div class="p">{_e(money(o["cur_price"]))}</div></a>'
        for o in others
    )
    was = ""
    if pct and pct >= 5:
        label = "En otras tiendas" if _vs_stores(deal) else "Normal"
        was = f'<div class="was">{label} <s>{_e(money(reference))}</s> · {pct}% menos</div>'
    elif p["cur_list_price"] and p["cur_list_price"] > price:
        was = f'<div class="was">La tienda dice que antes costaba {_e(money(p["cur_list_price"]))}</div>'
    badge_html = f'<span class="badge {badge[1]}">{_e(badge[0])}</span>' if badge else ""
    # Condición de pago: el precio grande siempre es el que vale con cualquier medio
    # de pago (el que se compara con otras tiendas); el de contado va aparte.
    cash = p.get("cur_cash_price")
    condition = (f'<div class="cond">Con tarjeta. En efectivo: <strong>{_e(money(cash))}</strong></div>'
                 if cash else "")
    img = (f'<img src="{_e(thumb(p["image"], 240))}" alt="{_e(p["name"])}" width="120" height="120" fetchpriority="high">'
           if p["image"] else "<span></span>")
    wa = whatsapp_url(p["name"], price, store, canonical, reference if pct and pct >= 5 else None, pct,
                      _vs_stores(deal))
    chart = chart_svg(hist, today)
    stats = ""
    if s30.days_covered:
        stats = (f'<p class="small muted">Últimos 30 días: mínimo {_e(money(s30.minimum))}, '
                 f'mediana {_e(money(s30.median))}, máximo {_e(money(s30.maximum))}.</p>')
    alert_form = f"""<h2>Avisame si baja</h2>
<form class="alert" id="alerta" data-pid="{pid}">
<label>Tu WhatsApp<input name="whatsapp" inputmode="tel" autocomplete="tel" placeholder="5555 5555" required></label>
<label>Precio que querés pagar<input name="target_price" inputmode="decimal" placeholder="{int(price * 0.9)}" required></label>
<label class="small consent"><input type="checkbox" name="consent" required> {_e(ALERT_CONSENT)}</label>
<button class="btn ghost" type="submit">Guardar alerta</button><p class="small muted" id="alerta-msg" role="status"></p></form>
<script>document.getElementById('alerta').addEventListener('submit',async e=>{{e.preventDefault();const f=e.target,m=document.getElementById('alerta-msg');
const r=await fetch('/api/alertas',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{product_id:+f.dataset.pid,whatsapp:f.whatsapp.value,target_price:f.target_price.value,consent:f.consent.checked}})}});
const j=await r.json().catch(()=>({{}}));m.textContent=r.ok?'Listo. Te avisamos cuando llegue a ese precio.':(j.detail||'No se pudo guardar.');}});</script>"""
    body = f"""<div class="hero">{img}<div><div class="small muted">{_e(store)}</div><h1>{_e(p["name"])}</h1>
<div class="price">{_e(money(price))}</div>{condition}{was}<div style="margin-top:8px">{badge_html}</div></div></div>
<div class="actions"><a class="btn" href="{_e(outbound(p["url"]))}" rel="nofollow noopener" target="_blank">Ver en {_e(store)}</a>
<a class="btn green" href="{_e(wa)}" target="_blank" rel="noopener">Compartir por WhatsApp</a>
<a class="btn ghost" href="/og/p/{pid}-9x16.png" download>Imagen para historias</a></div>
{('<h2>Historial de precio</h2>' + chart + stats) if chart else '<p class="small muted">Todavía no hay historial suficiente para este producto.</p>'}
{('<h2>En otras tiendas</h2><div class="rows">' + rows + '</div>') if rows else ''}
{alert_form}"""
    ld = {"@context": "https://schema.org", "@type": "Product", "name": p["name"],
          "image": p["image"] or None, "brand": p["brand"] or None, "gtin13": p["ean"] or None,
          "offers": {"@type": "Offer", "priceCurrency": "GTQ", "price": price, "url": p["url"],
                     "availability": "https://schema.org/InStock" if (p["cur_available"] or 0) > 0 or p["cur_available"] is None
                     else "https://schema.org/OutOfStock",
                     "seller": {"@type": "Organization", "name": store}}}
    ld = {k: v for k, v in ld.items() if v is not None}
    desc = f"{p['name']} a {money(price)} en {store}. Historial de precio y comparación con otras tiendas de Guatemala."
    page = layout(f"{p['name']} — {money(price)} en {store}", body, description=desc, canonical=canonical,
                  og_image=f"{SITE_URL}/og/p/{pid}.png", jsonld=ld)
    return HTMLResponse(page, headers={"Cache-Control": PAGE_CACHE})


@router.get("/ofertas", response_class=HTMLResponse)
async def ofertas(categoria: str = "", tienda: str = "") -> HTMLResponse:
    db = _db()
    categoria = categoria if categoria in taxonomy.LEVEL1 else ""
    stores = {s.key: s.name for s in load_stores()}
    tienda = tienda if tienda in stores else ""
    items = feed(db, category=categoria, store=tienda)
    cat_opts = "".join(f'<option value="{c}"{" selected" if c == categoria else ""}>{_e(taxonomy.LEVEL1_LABELS.get(c, c))}</option>'
                       for c in taxonomy.LEVEL1)
    store_opts = "".join(f'<option value="{k}"{" selected" if k == tienda else ""}>{_e(v)}</option>'
                         for k, v in sorted(stores.items(), key=lambda kv: kv[1]))
    body = f"""<h1>Ofertas de hoy</h1><p class="muted">Productos que hoy están por debajo de su propio precio normal, no del "precio antes" que muestra la tienda.</p>
<form class="filters" method="get"><select name="categoria" aria-label="Categoría" onchange="this.form.submit()"><option value="">Todas las categorías</option>{cat_opts}</select>
<select name="tienda" aria-label="Tienda" onchange="this.form.submit()"><option value="">Todas las tiendas</option>{store_opts}</select><noscript><button class="btn ghost">Filtrar</button></noscript></form>
<div class="rows">{"".join(_deal_row(d) for d in items) or '<p class="muted">No hay ofertas con estos filtros hoy.</p>'}</div>"""
    page = layout("Ofertas de hoy en Guatemala — Compa AI", body,
                  description="Ofertas reales detectadas hoy en tiendas de Guatemala, comparadas contra el historial de cada producto.",
                  canonical=f"{SITE_URL}/ofertas", og_image=f"{SITE_URL}/og-image.png", current="ofertas")
    return HTMLResponse(page, headers={"Cache-Control": PAGE_CACHE})


def daily_deal(db: Database) -> Optional[dict]:
    return db.query_one(
        """SELECT d.*, p.name, p.store_key, p.url, p.image, dp.day FROM daily_pick dp
           JOIN deals d ON d.id = dp.deal_id JOIN products p ON p.id = d.product_id
           ORDER BY dp.day DESC LIMIT 1"""
    )


@router.get("/oferta-del-dia")
async def oferta_del_dia() -> Response:
    db = _db()
    d = daily_deal(db)
    if d is None:
        body = '<h1>Oferta del día</h1><p class="muted">Todavía no elegimos la de hoy. Mientras tanto, mirá <a href="/ofertas">todas las ofertas</a>.</p>'
        return HTMLResponse(layout("Oferta del día — Compa AI", body, current="dia", noindex=True),
                            headers={"Cache-Control": "public, max-age=0, s-maxage=300"})
    return RedirectResponse(f"/p/{d['product_id']}", status_code=302,
                            headers={"Cache-Control": "public, max-age=0, s-maxage=300"})


def _og_deal(db: Database, pid: int) -> dict:
    p = _product(db, pid)
    today = today_utc()
    hist = series(db, pid, (date.fromisoformat(today) - timedelta(days=35)).isoformat())
    price = float(p["cur_price"] or 0)
    deal = _latest_deal(db, pid)
    reference = deal["reference"] if deal else window_stats(hist, today, 30, exclude_today=True).median
    pct = _pct(price, reference)
    points = []
    for i in range(30, 0, -1):
        day = (date.fromisoformat(today) - timedelta(days=i)).isoformat()
        for row in hist:
            if row["start_day"] <= day <= row["end_day"] and row["price"] and row["available"]:
                points.append((day, float(row["price"])))
                break
    return {"name": p["name"], "store": store_name(p["store_key"]), "price": price,
            "reference": reference, "drop_pct": (pct or 0) / 100, "series": points,
            "tag": None if pct and pct >= 5 else "Precio de hoy", "vs_stores": _vs_stores(deal)}


@router.get("/og/p/{name}")
async def og_image(name: str) -> Response:
    m = re.fullmatch(r"(\d+)(-9x16)?\.png", name)
    if not m:
        raise HTTPException(404)
    from . import og  # Pillow solo se carga en esta ruta: no pesa en el arranque en frío

    png = og.render(_og_deal(_db(), int(m.group(1))), vertical=bool(m.group(2)))
    return Response(png, media_type="image/png", headers={"Cache-Control": OG_CACHE})


# --- alertas ----------------------------------------------------------------
def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            or (request.client.host if request.client else "?"))


_ALERT_HITS: dict[str, list[float]] = {}


def normalize_whatsapp(raw: str) -> Optional[str]:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 8:
        digits = "502" + digits
    if len(digits) == 11 and digits.startswith("502") and digits[3] in "3456789":
        return "+" + digits
    return None


@router.post("/api/alertas")
async def crear_alerta(request: Request) -> JSONResponse:
    ip = _client_ip(request)
    now = time.time()
    hits = [t for t in _ALERT_HITS.get(ip, []) if now - t < 3600]
    if len(hits) >= 10:
        raise HTTPException(429, "Demasiados intentos. Probá más tarde.")
    _ALERT_HITS[ip] = hits + [now]
    try:
        data = await request.json()
        pid = int(data["product_id"])
        target = float(str(data["target_price"]).replace(",", "").replace("Q", "").strip())
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Revisá el precio.")
    if data.get("consent") is not True:
        raise HTTPException(422, "Necesitamos tu permiso para escribirte.")
    phone = normalize_whatsapp(str(data.get("whatsapp") or ""))
    if phone is None:
        raise HTTPException(422, "Ese número no parece de Guatemala (8 dígitos).")
    db = _db()
    p = _product(db, pid)
    if not 0 < target < float(p["cur_price"] or 0) * 3:
        raise HTTPException(422, "Revisá el precio.")
    existing = db.query_one(
        "SELECT id FROM alert_subscriptions WHERE whatsapp=? AND product_id=? AND status='active'",
        (phone, pid),
    )
    if existing:
        db.execute("UPDATE alert_subscriptions SET target_price=? WHERE id=?", (target, existing["id"]))
    else:
        db.execute(
            """INSERT INTO alert_subscriptions (whatsapp, product_id, target_price, consent_text, created_at)
               VALUES (?,?,?,?,datetime('now'))""",
            (phone, pid, target, ALERT_CONSENT),
        )
    return JSONResponse({"ok": True})


# --- panel ------------------------------------------------------------------
def _admin_token() -> str:
    return os.getenv("ADMIN_TOKEN", "")


def _admin_cookie(token: str) -> str:
    return hashlib.sha256(("gt-compare-admin:" + token).encode()).hexdigest()


def _is_admin(request: Request) -> bool:
    token = _admin_token()
    if not token:
        return False
    return hmac.compare_digest(request.cookies.get("gtc_admin", ""), _admin_cookie(token))


_LOGIN_HITS: dict[str, list[float]] = {}
LOGIN_ATTEMPTS = 5
LOGIN_WINDOW = 15 * 60


def _login_page(error: bool) -> HTMLResponse:
    msg = '<p class="small" style="color:var(--red)">Token incorrecto.</p>' if error else ""
    body = f"""<h1>Panel</h1>{msg}
<form class="alert" method="post" action="/admin/login">
<label>Token<input type="password" name="token" autocomplete="current-password" required></label>
<button class="btn" type="submit">Entrar</button></form>"""
    return HTMLResponse(layout("Panel — Compa AI", body, noindex=True),
                        headers={"Cache-Control": "private, no-store"})


@router.post("/admin/login")
async def admin_login(request: Request) -> Response:
    """El token viaja en el cuerpo del POST y vuelve como cookie httpOnly: nunca en la URL."""
    expected = _admin_token()
    if not expected:
        raise HTTPException(404)
    ip = _client_ip(request)
    now = time.time()
    hits = [t for t in _LOGIN_HITS.get(ip, []) if now - t < LOGIN_WINDOW]
    if len(hits) >= LOGIN_ATTEMPTS:
        raise HTTPException(429, "Demasiados intentos. Probá en 15 minutos.")
    token = parse_qs((await request.body()).decode() or "").get("token", [""])[0]
    if not hmac.compare_digest(token, expected):
        _LOGIN_HITS[ip] = hits + [now]
        return RedirectResponse("/admin?error=1", status_code=303, headers={"Cache-Control": "no-store"})
    _LOGIN_HITS.pop(ip, None)
    resp = RedirectResponse("/admin", status_code=303, headers={"Cache-Control": "no-store"})
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie("gtc_admin", _admin_cookie(expected), httponly=True, samesite="strict",
                    secure=https, path="/", max_age=7 * 86400)
    return resp


@router.post("/admin/logout")
async def admin_logout() -> Response:
    resp = RedirectResponse("/admin", status_code=303, headers={"Cache-Control": "no-store"})
    resp.delete_cookie("gtc_admin", path="/")
    return resp


@router.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, error: str = "") -> Response:
    if not _admin_token():
        raise HTTPException(404)
    if not _is_admin(request):
        return _login_page(error == "1")
    db = _db()
    pending = db.query(
        """SELECT d.*, p.name, p.store_key, p.url FROM deals d JOIN products p ON p.id=d.product_id
           WHERE d.status='pending' ORDER BY d.detected_on DESC, d.score DESC LIMIT 100"""
    )
    day = _feed_day(db) or today_utc()
    candidates = db.query(
        """SELECT d.*, p.name, p.store_key FROM deals d JOIN products p ON p.id=d.product_id
           WHERE d.detected_on=? AND (d.status='approved' OR (d.status='published' AND d.kind='oferta_fuerte'))
           ORDER BY d.score DESC LIMIT 20""",
        (day,),
    )
    # Sugerencia: atractivo por reglas (tramos de ahorro) y después score.
    candidates.sort(key=lambda d: (dschemas.expected_level(dschemas.daily_rules(dschemas.daily_state({
        "name": d["name"], "store": d["store_key"], "price": d["price"], "reference": d["reference"] or d["price"],
        "drop_pct": 1 - d["price"] / d["reference"] if d["reference"] else 0.0}))), d["score"]), reverse=True)
    pick = daily_deal(db)
    reviews = db.query(
        """SELECT r.*, a.name AS name_a, a.store_key AS store_a, b.name AS name_b, b.store_key AS store_b
           FROM match_reviews r JOIN products a ON a.id=r.product_a JOIN products b ON b.id=r.product_b
           WHERE r.status='pending' ORDER BY r.p_same DESC LIMIT 50"""
    )
    alerts = db.query_one("SELECT COUNT(*) AS n FROM alert_subscriptions WHERE status='active'")

    def btn(action: str, target: int, label: str, cls: str = "ghost") -> str:
        return (f'<form method="post" action="/admin/{action}/{target}" style="display:inline">'
                f'<button class="btn {cls}" style="min-height:36px">{label}</button></form>')

    def feats(d: dict) -> str:
        try:
            f = json.loads(d["features"] or "{}")
        except ValueError:
            f = {}
        return _e(", ".join(f"{k} {v}" for k, v in f.items()))

    rows_pending = "".join(
        f"""<tr><td><a href="{_e(d['url'])}" target="_blank" rel="noopener">{_e(d['name'])}</a><br>
<span class="small muted">{_e(store_name(d['store_key']))} · {d['detected_on']}</span></td>
<td>{_e(money(d['price']))}<br><span class="small muted">ref {_e(money(d['reference']))}</span></td>
<td>{d['score']}</td><td>{_e(d['cause'])}<br><span class="small muted">{_e(d['cause_source'])} {'' if d['cause_prob'] is None else f"P={d['cause_prob']:.2f}"}</span></td>
<td class="small muted">{feats(d)}</td><td>{btn('aprobar', d['id'], 'Aprobar', 'green')} {btn('rechazar', d['id'], 'Rechazar')}</td></tr>"""
        for d in pending
    )
    rows_cand = "".join(
        f"""<tr><td>{'<strong>Sugerida</strong> · ' if i == 0 else ''}{_e(d['name'])}<br><span class="small muted">{_e(store_name(d['store_key']))} · {_e(d['kind'])}</span></td>
<td>{_e(money(d['price']))}</td><td>{d['score']}</td><td>{btn('dia', d['id'], 'Elegir para hoy', 'green')}</td></tr>"""
        for i, d in enumerate(candidates)
    )
    rows_rev = "".join(
        f"""<tr><td>{_e(store_name(r['store_a']))}: {_e(r['name_a'])}<br>{_e(store_name(r['store_b']))}: {_e(r['name_b'])}</td>
<td>{'' if r['p_same'] is None else f"{r['p_same']:.2f}"}<br><span class="small muted">{_e(r['source'])}</span></td>
<td>{btn('match-si', r['id'], 'Mismo', 'green')} {btn('match-no', r['id'], 'Distinto')}</td></tr>"""
        for r in reviews
    )
    body = f"""<h1>Panel</h1><form method="post" action="/admin/logout"><button class="btn ghost" style="min-height:36px">Salir</button></form>
<p class="small muted">Alertas activas: {alerts['n'] if alerts else 0}.
Envío de alertas: {'encendido' if os.getenv('ALERTS_SEND_ENABLED') == '1' else 'apagado'}.
Oferta del día actual: {_e(pick['name']) if pick else 'ninguna'}.</p>
<h2>Posibles price errors por aprobar ({len(pending)})</h2>
<table><tr><th>Producto</th><th>Precio</th><th>Score</th><th>Causa</th><th>Señales</th><th></th></tr>{rows_pending or '<tr><td colspan=6 class="muted">Nada pendiente.</td></tr>'}</table>
<h2>Candidatas a oferta del día ({day})</h2>
<table><tr><th>Producto</th><th>Precio</th><th>Score</th><th></th></tr>{rows_cand or '<tr><td colspan=4 class="muted">Sin candidatas.</td></tr>'}</table>
<h2>Matches por revisar ({len(reviews)})</h2>
<table><tr><th>Par</th><th>P(mismo)</th><th></th></tr>{rows_rev or '<tr><td colspan=3 class="muted">Nada pendiente.</td></tr>'}</table>"""
    return HTMLResponse(layout("Panel — Compa AI", body, noindex=True),
                        headers={"Cache-Control": "private, no-store"})


@router.post("/admin/{action}/{target}")
async def admin_action(request: Request, action: str, target: int) -> Response:
    if not _is_admin(request):
        raise HTTPException(404)
    db = _db()
    note = parse_qs((await request.body()).decode() or "").get("note", [""])[0][:500]
    if action in ("aprobar", "rechazar"):
        db.execute("UPDATE deals SET status=?, reviewed_at=datetime('now'), note=? WHERE id=?",
                   ("approved" if action == "aprobar" else "rejected", note or None, target))
    elif action == "dia":
        db.execute("INSERT OR REPLACE INTO daily_pick (day, deal_id, chosen_at) VALUES (?,?,datetime('now'))",
                   (today_utc(), target))
    elif action in ("match-si", "match-no"):
        db.execute("UPDATE match_reviews SET status=? WHERE id=?",
                   ("accepted" if action == "match-si" else "rejected", target))
    else:
        raise HTTPException(404)
    return RedirectResponse("/admin", status_code=303, headers={"Cache-Control": "no-store"})
