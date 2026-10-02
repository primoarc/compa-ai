"""Historial de precios por intervalos y estadísticas sobre él.

Cada producto tiene intervalos [start_day, end_day] a un mismo precio, precio
de lista y disponibilidad. Una corrida que ve lo mismo extiende el intervalo
abierto; si cambia algo, abre uno nuevo. Si un producto no aparece durante más
de GAP_DAYS, el siguiente avistamiento abre un intervalo nuevo aunque el precio
sea igual: no se supone que siguió igual durante el hueco.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from .db import Database

GAP_DAYS = 3
# Un producto sin cambios se marca como visto cada EXTEND_EVERY días (cada fila
# escrita cuenta en el plan de Turso). Los que se muestran como oferta, oferta
# del día o están en la cola del panel se marcan todos los días.
EXTEND_EVERY = 2
URL_SKU_PREFIX = "url:"


# Un precio visto hace más de FRESH_DAYS días no se muestra como oferta ni lleva
# badge: una corrida parcial deja productos sin refrescar y su precio puede ya
# no existir.
FRESH_DAYS = 2


def fresh_since(day: str) -> str:
    """Último día aceptable para el end_day de un precio que se muestra como
    vigente. end_day nunca es posterior al último avistamiento, así que el
    corte nunca deja pasar un precio más viejo (puede dejar fuera alguno de
    justo 2 días cuyo intervalo no se extendió)."""
    return (_d(day) - timedelta(days=FRESH_DAYS)).isoformat()


def seen_since(day: str) -> str:
    """Primer día que cuenta como "visto hoy": con marcado cada EXTEND_EVERY días,
    un producto sin cambios puede tener su intervalo cerrado hasta ayer."""
    return (_d(day) - timedelta(days=EXTEND_EVERY - 1)).isoformat()


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _d(day: str) -> date:
    return date.fromisoformat(day)


@dataclass
class Observation:
    product_id: int
    price: Optional[float]
    list_price: Optional[float]
    available: Optional[int]
    cash_price: Optional[float] = None  # precio solo en efectivo, si la tienda lo separa


def _same(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < 0.005


def _values_equal(row: dict, obs: Observation) -> bool:
    return (
        _same(row["price"], obs.price)
        and _same(row["list_price"], obs.list_price)
        and (row["available"] or 0) == (obs.available or 0)
        and _same(row.get("cash_price"), obs.cash_price)
    )


def plan_interval_writes(
    open_rows: dict[int, dict], day: str, observations: Iterable[Observation],
    daily: frozenset[int] = frozenset(),
) -> tuple[list[tuple], list[tuple], list[tuple], set[int]]:
    """Decide qué escribir para una corrida, sin tocar la base.

    Devuelve (extender, recortar, insertar, cambiados):
      extender: (end_day, product_id, start_day)
      recortar: (end_day, product_id, start_day)  cierra el intervalo previo
      insertar: (product_id, start_day, end_day, price, list_price, available, cash_price)
    `open_rows` es el último intervalo de cada producto.
    """
    extend: list[tuple] = []
    trim: list[tuple] = []
    insert: list[tuple] = []
    changed: set[int] = set()
    today = _d(day)
    for obs in observations:
        last = open_rows.get(obs.product_id)
        if last is None:
            insert.append((obs.product_id, day, day, obs.price, obs.list_price, obs.available, obs.cash_price))
            changed.add(obs.product_id)
            continue
        start, end = _d(last["start_day"]), _d(last["end_day"])
        if today < start:
            continue  # observación más vieja que lo guardado: se ignora
        if _values_equal(last, obs):
            if (today - end).days <= GAP_DAYS:
                lag = (today - end).days
                if lag >= EXTEND_EVERY or (lag >= 1 and obs.product_id in daily):
                    extend.append((day, obs.product_id, last["start_day"]))
                continue
            insert.append((obs.product_id, day, day, obs.price, obs.list_price, obs.available, obs.cash_price))
            changed.add(obs.product_id)
            continue
        changed.add(obs.product_id)
        if today == start:
            # misma fecha de inicio: la corrida nueva corrige el valor del día
            trim.append((None, obs.product_id, last["start_day"]))
            insert.append((obs.product_id, day, day, obs.price, obs.list_price, obs.available, obs.cash_price))
            continue
        if today <= end:
            trim.append(((today - timedelta(days=1)).isoformat(), obs.product_id, last["start_day"]))
        insert.append((obs.product_id, day, day, obs.price, obs.list_price, obs.available, obs.cash_price))
    return extend, trim, insert, changed


def open_intervals(db: Database, product_ids: Sequence[int]) -> dict[int, dict]:
    """Último intervalo de cada producto."""
    out: dict[int, dict] = {}
    ids = list(product_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = db.query(
            f"""SELECT h.* FROM price_history h
                JOIN (SELECT product_id, MAX(start_day) AS s FROM price_history
                      WHERE product_id IN ({marks}) GROUP BY product_id) m
                  ON m.product_id = h.product_id AND m.s = h.start_day""",
            chunk,
        )
        for r in rows:
            out[r["product_id"]] = r
    return out


def apply_observations(
    db: Database, day: str, observations: list[Observation], run_id: Optional[int],
    daily: frozenset[int] = frozenset(),
) -> int:
    """Escribe una corrida en el historial. Devuelve cuántos productos cambiaron.
    `daily`: productos que se marcan como vistos hoy aunque no hayan cambiado."""
    if not observations:
        return 0
    rows = open_intervals(db, [o.product_id for o in observations])
    extend, trim, insert, changed = plan_interval_writes(rows, day, observations, daily)
    with db.transaction():
        if extend:
            db.executemany(
                "UPDATE price_history SET end_day=? WHERE product_id=? AND start_day=?", extend
            )
        for end_day, pid, start_day in trim:
            if end_day is None:
                db.execute(
                    "DELETE FROM price_history WHERE product_id=? AND start_day=?", (pid, start_day)
                )
            else:
                db.execute(
                    "UPDATE price_history SET end_day=? WHERE product_id=? AND start_day=?",
                    (end_day, pid, start_day),
                )
        if insert:
            db.executemany(
                """INSERT OR REPLACE INTO price_history
                   (product_id, start_day, end_day, price, list_price, available, cash_price, run_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [row + (run_id,) for row in insert],
            )
    return len(changed)


def series(db: Database, product_id: int, since_day: Optional[str] = None) -> list[dict]:
    if since_day:
        return db.query(
            """SELECT * FROM price_history WHERE product_id=? AND end_day>=?
               ORDER BY start_day""",
            (product_id, since_day),
        )
    return db.query(
        "SELECT * FROM price_history WHERE product_id=? ORDER BY start_day", (product_id,)
    )


@dataclass
class WindowStats:
    median: Optional[float]
    minimum: Optional[float]
    maximum: Optional[float]
    p20: Optional[float]
    days_covered: int
    intervals: int
    min_day: Optional[str]


def _weighted_quantile(pairs: list[tuple[float, int]], q: float) -> Optional[float]:
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    if total <= 0:
        return None
    target = q * total
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= target:
            return value
    return pairs[-1][0]


def window_stats(
    intervals: Iterable[dict], today: str, days: int = 30, *, exclude_today: bool = False
) -> WindowStats:
    """Estadísticas ponderadas por días sobre una ventana que termina en `today`.

    Con `exclude_today` la ventana termina el día anterior, que es lo que hace
    falta para comparar el precio de hoy contra "su precio normal" sin que el
    propio precio de hoy tire de la mediana.
    """
    end = _d(today) - timedelta(days=1 if exclude_today else 0)
    start = end - timedelta(days=days - 1)
    pairs: list[tuple[float, int]] = []
    min_day: Optional[str] = None
    minimum: Optional[float] = None
    count = 0
    for row in intervals:
        price = row.get("price")
        if price is None or not row.get("available"):
            continue
        a = max(_d(row["start_day"]), start)
        b = min(_d(row["end_day"]), end)
        if b < a:
            continue
        weight = (b - a).days + 1
        pairs.append((float(price), weight))
        count += 1
        if minimum is None or price < minimum:
            minimum, min_day = float(price), a.isoformat()
    if not pairs:
        return WindowStats(None, None, None, None, 0, 0, None)
    return WindowStats(
        median=_weighted_quantile(pairs, 0.5),
        minimum=minimum,
        maximum=max(v for v, _ in pairs),
        p20=_weighted_quantile(pairs, 0.2),
        days_covered=sum(w for _, w in pairs),
        intervals=count,
        min_day=min_day,
    )


def previous_price(intervals: Sequence[dict], today: str) -> Optional[float]:
    """Precio del intervalo anterior al vigente hoy (la "corrida anterior")."""
    ordered = sorted(intervals, key=lambda r: r["start_day"])
    current = None
    for i, row in enumerate(ordered):
        if row["start_day"] <= today <= row["end_day"]:
            current = i
    if current is None or current == 0:
        return None
    prev = ordered[current - 1]
    return prev.get("price")
