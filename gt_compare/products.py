"""Productos de tienda en la base: normalización y upsert por lotes."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from . import matching
from .db import Database
from .history import URL_SKU_PREFIX
from .ingest.types import ProductRecord

_DIGITS = re.compile(r"\D")

# Siman y Max publican artículos no vendibles a Q8.000.000–Q99.999.999. Lo más caro
# real que vimos son servidores de ~Q240.000 en Kemik.
MAX_PLAUSIBLE_PRICE = 1_000_000.0


def plausible_price(price: Optional[float]) -> bool:
    return price is not None and 0 < price < MAX_PLAUSIBLE_PRICE


def gs1_check_ok(code: str) -> bool:
    """Dígito verificador GS1 (EAN-8/UPC-A/EAN-13/GTIN-14)."""
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_ean(raw: Optional[str]) -> Optional[str]:
    """GTIN normalizado a 13 dígitos, o None si no es un código válido.

    Se descartan los rangos de circulación restringida (prefijos 02 y 2xx en
    EAN-13): son códigos internos de tienda o de peso variable, no identifican
    el mismo producto entre tiendas distintas.
    """
    if not raw:
        return None
    code = _DIGITS.sub("", str(raw))
    if len(code) not in (8, 12, 13, 14) or set(code) == {"0"}:
        return None
    if not gs1_check_ok(code):
        return None
    if len(code) == 14:
        if code[0] != "0":
            return None  # GTIN-14 de empaque logístico, no la unidad
        code = code[1:]
    elif len(code) == 12:
        code = "0" + code
    elif len(code) == 8:
        return code  # EAN-8 se deja como está
    if code.startswith("2") or code.startswith("02"):
        return None
    return code


def best_model_code(name: str) -> Optional[str]:
    codes = matching.model_codes(name or "")
    return max(codes, key=len) if codes else None


def _existing_rows(db: Database, store_key: str, skus: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for i in range(0, len(skus), 500):
        chunk = skus[i : i + 500]
        marks = ",".join("?" * len(chunk))
        for row in db.query(
            f"SELECT * FROM products WHERE store_key=? AND store_sku IN ({marks})",
            [store_key, *chunk],
        ):
            found[row["store_sku"]] = row
    return found


def _same_value(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(float(a) - float(b)) < 0.005
    return a == b


def _changes(row: dict, rec: ProductRecord) -> dict:
    """Columnas distintas de lo guardado. Campos opcionales en None no borran nada."""
    wanted = {
        "url": rec.url, "name": rec.name, "cur_price": rec.price, "cur_list_price": rec.list_price,
        "cur_available": rec.available, "promo_text": rec.promo_text, "cur_cash_price": rec.cash_price,
    }
    for field, value in (("brand", rec.brand), ("ean", normalize_ean(rec.ean)),
                         ("image", rec.image), ("store_category", rec.store_category)):
        if value is not None:
            wanted[field] = value
    return {k: v for k, v in wanted.items() if not _same_value(row.get(k), v)}


def upsert_products(db: Database, records: Iterable[ProductRecord], day: str) -> dict[str, int]:
    """Inserta o actualiza SKUs. Devuelve {store_sku: product_id}.

    Solo escribe filas nuevas o con algún dato distinto, y en las existentes solo
    las columnas que cambiaron: Turso cobra cada índice reescrito (url, ean) y el
    de búsqueda (name). "Lo vi hoy" ya queda en el historial (el intervalo que
    termina hoy). `last_seen` es la última vez que cambió algo del producto.

    Si la tienda tiene una fila heredada del barrido viejo (identificada por URL
    y solo con el primer SKU), el primer SKU real de ese producto la adopta, así
    el historial rellenado queda unido al SKU correcto.
    """
    records = list(records)
    if not records:
        return {}
    store_key = records[0].store_key
    existing = _existing_rows(db, store_key, [r.store_sku for r in records])
    legacy = {
        row["store_sku"][len(URL_SKU_PREFIX):]: row["id"]
        for row in db.query(
            "SELECT id, store_sku FROM products WHERE store_key=? AND store_sku LIKE ?",
            (store_key, URL_SKU_PREFIX + "%"),
        )
    }
    adopt: list[tuple] = []
    if legacy:
        for rec in records:
            if rec.first_sku and rec.url in legacy and rec.store_sku not in existing:
                adopt.append((rec.store_sku, legacy.pop(rec.url)))
    new: list[ProductRecord] = []  # incluye las filas recién adoptadas
    updates: dict[tuple[str, ...], list[tuple]] = {}  # columnas -> valores
    for rec in {r.store_sku: r for r in records}.values():
        row = existing.get(rec.store_sku)
        if row is None:
            new.append(rec)
            continue
        diff = _changes(row, rec)
        if not diff:
            continue
        if "name" in diff and best_model_code(rec.name):
            diff["model_code"] = best_model_code(rec.name)
        diff["last_seen"] = day
        updates.setdefault(tuple(diff), []).append((*diff.values(), row["id"]))
    with db.transaction():
        if adopt:
            db.executemany("UPDATE products SET store_sku=?, sku_level=1 WHERE id=?", adopt)
        for columns, values in updates.items():
            assignments = ", ".join(f"{c}=?" for c in columns)
            db.executemany(f"UPDATE products SET {assignments} WHERE id=?", values)
        if new:
            db.executemany(
                """INSERT INTO products
                   (store_key, store_sku, url, name, brand, ean, model_code, image,
                    store_category, cur_price, cur_list_price, cur_available, cur_cash_price,
                    promo_text, first_seen, last_seen, sku_level)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                   ON CONFLICT(store_key, store_sku) DO UPDATE SET
                     url=excluded.url, name=excluded.name,
                     brand=COALESCE(excluded.brand, products.brand),
                     ean=COALESCE(excluded.ean, products.ean),
                     model_code=COALESCE(excluded.model_code, products.model_code),
                     image=COALESCE(excluded.image, products.image),
                     store_category=COALESCE(excluded.store_category, products.store_category),
                     cur_price=excluded.cur_price, cur_list_price=excluded.cur_list_price,
                     cur_available=excluded.cur_available, cur_cash_price=excluded.cur_cash_price,
                     promo_text=excluded.promo_text, last_seen=excluded.last_seen""",
                [
                    (
                        rec.store_key, rec.store_sku, rec.url, rec.name, rec.brand,
                        normalize_ean(rec.ean), best_model_code(rec.name), rec.image,
                        rec.store_category, rec.price, rec.list_price, rec.available,
                        rec.cash_price, rec.promo_text, day, day,
                    )
                    for rec in new
                ],
            )
    ids = {sku: row["id"] for sku, row in existing.items()}
    missing = [r.store_sku for r in records if r.store_sku not in ids]
    for i in range(0, len(missing), 500):
        chunk = missing[i : i + 500]
        marks = ",".join("?" * len(chunk))
        for row in db.query(
            f"SELECT id, store_sku FROM products WHERE store_key=? AND store_sku IN ({marks})",
            [store_key, *chunk],
        ):
            ids[row["store_sku"]] = row["id"]
    return ids
