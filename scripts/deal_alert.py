#!/usr/bin/env python3
"""Escaneo de precios anómalos con aviso por Telegram.

Pensado para correr desde cron. Avisa SOLO de hallazgos de confianza alta y
SOLO cuando son nuevos, porque un bot que repite los mismos tres hallazgos
cada seis horas se silencia a la semana y deja de servir.

Estado en ~/.gt-compare/deal_alerts.json: recuerda lo ya avisado y su precio,
así que un producto vuelve a sonar solo si bajó todavía más.

Uso:
    python scripts/deal_alert.py            # escanea y avisa si hay novedad
    python scripts/deal_alert.py --dry      # imprime, no manda nada
    python scripts/deal_alert.py --test     # manda un mensaje de prueba
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gt_compare import deals, planner, vtex  # noqa: E402
from gt_compare.stores import load_stores  # noqa: E402
from gt_compare.web import _best_per_store  # noqa: E402

TELEGRAM_DIR = Path.home() / ".claude" / "channels" / "telegram"
STATE_PATH = Path.home() / ".gt-compare" / "deal_alerts.json"

WATCHLIST = [
    "televisor 65", "televisor 55", "televisor 75",
    "laptop", "refrigeradora", "lavadora", "mini split",
    "playstation 5", "airpods", "monitor",
]

# Un producto ya avisado vuelve a sonar solo si baja al menos esto.
REALERT_DROP = 0.10
# El estado se poda para que no crezca sin control.
STATE_MAX_ENTRIES = 400


def _bot_token() -> str:
    env = TELEGRAM_DIR / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("No encontré TELEGRAM_BOT_TOKEN en " + str(env))


def _chat_ids() -> list:
    cfg = json.loads((TELEGRAM_DIR / "access.json").read_text(encoding="utf-8"))
    ids = [str(x) for x in cfg.get("allowFrom", [])]
    if not ids:
        raise SystemExit("No hay destinatarios en access.json")
    return ids


def send(text: str) -> None:
    token = _bot_token()
    for chat_id in _chat_ids():
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=payload
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
            if not body.get("ok"):
                print("telegram error:", body, file=sys.stderr)


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    if len(state) > STATE_MAX_ENTRIES:
        # se conservan los más recientes
        state = dict(sorted(state.items(), key=lambda kv: kv[1].get("ts", ""))[-STATE_MAX_ENTRIES:])
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


async def scan() -> list:
    stores = load_stores()
    found = []
    for q in WATCHLIST:
        try:
            plan = planner.local_plan(q)
            results = await vtex.search_all(
                stores, q, timeout=12, ttl_seconds=30 * 60, plan=plan
            )
            rows = _best_per_store(q, results, plan=plan)
            found.extend(
                a for a in deals.find_anomalies(q, rows) if a.confidence == "alta"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[{q}] error: {exc}", file=sys.stderr)
    return found


def key_of(a) -> str:
    return f"{a.store_key}|{a.name[:90]}"


def novedades(found: list, state: dict) -> list:
    out = []
    for a in found:
        prev = state.get(key_of(a))
        if prev is None:
            out.append(a)
        elif a.price <= prev.get("price", 0) * (1 - REALERT_DROP):
            out.append(a)  # bajó todavía más: vuelve a valer la pena
    return out


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render(found: list) -> str:
    lines = [f"<b>🎯 {len(found)} hallazgo(s) de confianza alta</b>", ""]
    for a in found:
        pct = round(a.discount * 100)
        ref = (
            f"la tienda lo lista en Q{a.reference:,.0f}"
            if a.kind == "lista"
            else f"el mismo modelo está a Q{a.reference:,.0f} en {a.peers} tienda(s)"
        )
        lines += [
            f"<b>−{pct}%</b>  {esc(a.store)}",
            esc(a.name[:110]),
            f"<b>Q{a.price:,.0f}</b> · {ref}",
            f'<a href="{esc(a.url)}">abrir</a>',
            "",
        ]
    lines.append("<i>Verificá el precio en la tienda antes de ordenar.</i>")
    return "\n".join(lines)


def main() -> None:
    if "--test" in sys.argv:
        send("🎯 Cacería de precios: el canal quedó conectado.")
        print("mensaje de prueba enviado")
        return

    found = asyncio.run(scan())
    state = load_state()
    nuevos = novedades(found, state)

    print(f"{datetime.now():%Y-%m-%d %H:%M}  alta={len(found)}  nuevos={len(nuevos)}")
    if not nuevos:
        return

    text = render(nuevos)
    if "--dry" in sys.argv:
        print(text)
        return

    send(text)
    for a in nuevos:
        state[key_of(a)] = {
            "price": a.price,
            "store": a.store,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
    save_state(state)


if __name__ == "__main__":
    main()
