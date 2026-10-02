"""Imágenes para compartir una oferta: 1200x630 (OG) y 1080x1920 (historias 9:16).

Se dibujan con Pillow y Noto Sans incluida en el paquete: en Vercel no hay
fuentes de sistema y la fuente por defecto de Pillow no trae tildes.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

INK = (11, 11, 12)
MUTED = (118, 118, 124)
RULE = (228, 228, 231)
GREEN = (10, 107, 71)
GREEN_SOFT = (233, 244, 239)
WHITE = (255, 255, 255)


FONTS = Path(__file__).parent / "fonts"


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf")), size)


def _money(v: Optional[float]) -> str:
    return f"Q{v:,.2f}" if v is not None else ""


def _wrap(draw: ImageDraw.ImageDraw, text: str, size: int, width: int, max_lines: int) -> list[str]:
    font = _font(size, True)
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = w
        if len(lines) == max_lines:
            break
    if line and len(lines) < max_lines:
        lines.append(line)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int,
          fill=INK, bold: bool = False) -> None:
    draw.text(xy, text, font=_font(size, bold), fill=fill)


def _chart(d: ImageDraw.ImageDraw, series: list[tuple[str, float]], price: float,
           box: tuple[int, int, int, int]) -> None:
    """Escalera de precios diarios; el punto final es el precio de hoy."""
    x0, y0, x1, y1 = box
    if y1 - y0 < 200 or len(series) < 2:
        return
    values = [p for _, p in series] + [price]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = (x1 - x0) / (len(values) - 1)

    def yv(v: float) -> float:
        return y1 - 50 - (v - lo) / span * (y1 - y0 - 110)

    pts = []
    for i, v in enumerate(values):
        x = x0 + i * step
        if pts:
            pts.append((x, pts[-1][1]))
        pts.append((x, yv(v)))
    d.line(pts, fill=INK, width=5, joint="curve")
    ex, ey = pts[-1]
    d.ellipse((ex - 14, ey - 14, ex + 14, ey + 14), fill=GREEN)
    _text(d, (x0, y0), f"Últimos {len(series)} días", 34, fill=MUTED)
    _text(d, (x0, y1 - 36), f"Máximo {_money(hi)}", 30, fill=MUTED)


def render(deal: dict, *, vertical: bool = False) -> bytes:
    """deal: name, store, price, reference, drop_pct (0..1); opcionales series [(día, precio)], tag, vs_stores,
    against ("bajo su precio anterior") y ref_label ("Antes")."""
    w, h = (1080, 1920) if vertical else (1200, 630)
    pad = 96 if vertical else 72
    img = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(img)
    pct = round(deal["drop_pct"] * 100)

    _text(d, (pad, pad), "Compa AI", 44 if vertical else 34, bold=True)
    against = deal.get("against") or ("menos que en otras tiendas" if deal.get("vs_stores") else "bajo su precio normal")
    tag = deal.get("tag") or f"{pct}% {against}"
    tag_size = 40 if vertical else 30
    tw = int(d.textlength(tag, font=_font(tag_size, True)))
    y_tag = pad + (110 if vertical else 76)
    d.rounded_rectangle((pad, y_tag, pad + tw + 44, y_tag + tag_size + 34), radius=8, fill=GREEN_SOFT)
    _text(d, (pad + 22, y_tag + 10), tag, tag_size, fill=GREEN, bold=True)

    name_size = 64 if vertical else 46
    y = y_tag + tag_size + (90 if vertical else 56)
    for line in _wrap(d, deal["name"], name_size, w - 2 * pad, 4 if vertical else 2):
        _text(d, (pad, y), line, name_size, bold=True)
        y += int(name_size * 1.22)

    price_size = 150 if vertical else 96
    y_price = (h - pad - price_size - 300) if vertical else (h - pad - price_size - 56)
    if vertical and deal.get("series"):
        _chart(d, deal["series"], deal["price"], (pad, y + 60, w - pad, y_price - 90))
    _text(d, (pad, y_price), _money(deal["price"]), price_size, fill=GREEN, bold=True)
    small = 40 if vertical else 28
    y_meta = y_price + int(price_size * 1.4)
    meta = f"en {deal['store']}"
    if deal.get("reference") and not deal.get("tag"):
        label = deal.get("ref_label") or ("En otras tiendas" if deal.get("vs_stores") else "Precio normal")
        meta = f"{label} {_money(deal['reference'])}  ·  " + meta
    _text(d, (pad, y_meta), meta, small, fill=MUTED)
    if vertical:
        d.line((pad, h - pad - 90, w - pad, h - pad - 90), fill=RULE, width=2)
        _text(d, (pad, h - pad - 60), "Compará precios en gt-compare.vercel.app", 36, fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
