"""Acceso a la base del historial.

Dos backends con el mismo SQL (dialecto SQLite):
  - archivo local con sqlite3 (ingesta por cron y vista previa local);
  - Turso por su API HTTP (producción en Vercel), sin dependencias nuevas.

`GT_COMPARE_DB_URL` elige: `libsql://…` o `https://…` usa Turso con
`TURSO_AUTH_TOKEN`; cualquier otra cosa es una ruta de archivo. En Vercel sin
configurar, `get_db()` devuelve None y el sitio funciona como antes.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

import httpx

DEFAULT_PATH = Path.home() / ".gt-compare" / "history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_key TEXT NOT NULL,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  pages INTEGER NOT NULL DEFAULT 0,
  seen INTEGER NOT NULL DEFAULT 0,
  changed INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_key TEXT NOT NULL,
  store_sku TEXT NOT NULL,
  url TEXT NOT NULL,
  name TEXT NOT NULL,
  brand TEXT,
  ean TEXT,
  model_code TEXT,
  image TEXT,
  store_category TEXT,
  category_id TEXT,
  category_source TEXT,
  category_conf REAL,
  cur_price REAL,
  cur_list_price REAL,
  cur_available INTEGER,
  cur_cash_price REAL,
  promo_text TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  sku_level INTEGER NOT NULL DEFAULT 1,
  UNIQUE (store_key, store_sku)
);
CREATE INDEX IF NOT EXISTS ix_products_ean ON products (ean);
CREATE INDEX IF NOT EXISTS ix_products_url ON products (store_key, url);
CREATE INDEX IF NOT EXISTS ix_products_cat ON products (category_id);

-- Historial como intervalos: un precio vale desde start_day hasta end_day
-- (inclusive, fechas YYYY-MM-DD). Si la corrida siguiente ve lo mismo se
-- extiende end_day; si cambia, se abre otro intervalo.
CREATE TABLE IF NOT EXISTS price_history (
  product_id INTEGER NOT NULL,
  start_day TEXT NOT NULL,
  end_day TEXT NOT NULL,
  price REAL,
  list_price REAL,
  available INTEGER,
  cash_price REAL,
  run_id INTEGER,
  PRIMARY KEY (product_id, start_day)
);
-- "Visto hoy" = tiene un intervalo que termina hoy. Así la ingesta no reescribe
-- cada producto a diario solo para marcarlo como visto.
CREATE INDEX IF NOT EXISTS ix_ph_end ON price_history (end_day);

CREATE TABLE IF NOT EXISTS clusters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ean TEXT,
  name TEXT,
  brand TEXT,
  category_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_clusters_ean ON clusters (ean);

CREATE TABLE IF NOT EXISTS product_clusters (
  product_id INTEGER PRIMARY KEY,
  cluster_id INTEGER NOT NULL,
  method TEXT NOT NULL,
  confidence REAL NOT NULL,
  decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pc_cluster ON product_clusters (cluster_id);

CREATE TABLE IF NOT EXISTS match_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_a INTEGER NOT NULL,
  product_b INTEGER NOT NULL,
  p_same REAL,
  level REAL,
  source TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  UNIQUE (product_a, product_b)
);

CREATE TABLE IF NOT EXISTS decision_cache (
  key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  value TEXT,
  probability REAL,
  confidence REAL,
  source TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  detected_on TEXT NOT NULL,
  kind TEXT NOT NULL,
  score REAL NOT NULL,
  price REAL NOT NULL,
  reference REAL,
  features TEXT,
  cause TEXT,
  cause_prob REAL,
  cause_source TEXT,
  status TEXT NOT NULL,
  reviewed_at TEXT,
  note TEXT,
  UNIQUE (product_id, detected_on)
);
CREATE INDEX IF NOT EXISTS ix_deals_status ON deals (status, detected_on);

CREATE TABLE IF NOT EXISTS daily_pick (
  day TEXT PRIMARY KEY,
  deal_id INTEGER NOT NULL,
  chosen_at TEXT NOT NULL
);

-- Filas escritas por mes, sumadas por cada corrida (límite del plan de Turso).
CREATE TABLE IF NOT EXISTS db_usage (
  month TEXT PRIMARY KEY,
  rows_written INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alert_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  whatsapp TEXT NOT NULL,
  product_id INTEGER NOT NULL,
  target_price REAL NOT NULL,
  consent_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);

-- Búsqueda por nombre (tiendas que se consultan desde el catálogo, no en vivo).
CREATE VIRTUAL TABLE IF NOT EXISTS product_search USING fts5(
  name, content='products', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS products_search_ai AFTER INSERT ON products BEGIN
  INSERT INTO product_search (rowid, name) VALUES (new.id, new.name);
END;
CREATE TRIGGER IF NOT EXISTS products_search_ad AFTER DELETE ON products BEGIN
  INSERT INTO product_search (product_search, rowid, name) VALUES ('delete', old.id, old.name);
END;
CREATE TRIGGER IF NOT EXISTS products_search_au AFTER UPDATE OF name ON products BEGIN
  INSERT INTO product_search (product_search, rowid, name) VALUES ('delete', old.id, old.name);
  INSERT INTO product_search (rowid, name) VALUES (new.id, new.name);
END;
"""


class Database:
    """Interfaz mínima común a los dos backends.

    `rows_written` cuenta las filas que tocaron las sentencias de este proceso
    (sin las que tocan los triggers): es lo que mide el plan de Turso.
    """

    rows_written = 0

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        raise NotImplementedError

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        raise NotImplementedError

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        raise NotImplementedError

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def script(self, sql: str) -> None:
        raise NotImplementedError

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        yield self

    def close(self) -> None:
        pass

    def migrate(self) -> None:
        had_search = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='product_search'"
        )
        self.script(SCHEMA)
        # Columnas agregadas después de la primera versión del esquema.
        for table, column in (("products", "cur_cash_price"), ("price_history", "cash_price")):
            if column not in {r["name"] for r in self.query(f"PRAGMA table_info({table})")}:
                self.execute(f"ALTER TABLE {table} ADD COLUMN {column} REAL")
        if not had_search:
            self.execute("INSERT INTO product_search (product_search) VALUES ('rebuild')")


class SQLiteDatabase(Database):
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.RLock()
        self._depth = 0

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self.rows_written += max(cur.rowcount, 0)
            return cur.lastrowid if cur.lastrowid is not None else cur.rowcount

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            cur = self._conn.executemany(sql, [tuple(r) for r in rows])
            self.rows_written += max(cur.rowcount, 0)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def script(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        self._conn.close()


def _strip_comments(sql: str) -> str:
    """Quita comentarios `--` y `/* */` sin tocar lo que está entre comillas."""
    out: list[str] = []
    i, n = 0, len(sql)
    quote: Optional[str] = None
    while i < n:
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:  # comilla escapada: '' o ""
                    out.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def split_sql(sql: str) -> list[str]:
    """Separa un script en sentencias para la API HTTP de Turso.

    Un ";" solo cierra una sentencia cuando SQLite la da por completa, así que
    no se corta dentro de strings ni del cuerpo de un trigger. Los comentarios
    se quitan antes: un ";" dentro de un comentario cortó el esquema en dos la
    primera vez que se migró contra Turso.
    """
    out: list[str] = []
    buf = ""
    for part in _strip_comments(sql).split(";"):
        buf += part + ";"
        if sqlite3.complete_statement(buf):
            stmt = buf.strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            buf = ""
    rest = buf.rstrip(";").strip()
    if rest:
        out.append(rest)
    return out


def _turso_arg(value: Any) -> dict:
    # Formato de argumentos de la API HTTP de Turso: los enteros viajan como
    # texto para no perder precisión; los floats como número.
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _turso_value(cell: dict) -> Any:
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    return cell.get("value")


class TursoDatabase(Database):
    """Turso (libSQL) por HTTP: `POST {url}/v2/pipeline`."""

    def __init__(self, url: str, token: str, *, timeout: float = 15.0,
                 transport: Optional[httpx.BaseTransport] = None):
        base = url.replace("libsql://", "https://").rstrip("/")
        self._client = httpx.Client(
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    def _pipeline(self, stmts: list[tuple[str, Sequence[Any]]]) -> list[dict]:
        requests = [
            {"type": "execute", "stmt": {"sql": sql, "args": [_turso_arg(p) for p in params]}}
            for sql, params in stmts
        ]
        requests.append({"type": "close"})
        # Los errores de httpx traen la URL completa de la base; en un log público
        # (Actions) eso expone el host. Se relanzan sin URL.
        try:
            resp = self._client.post("/v2/pipeline", json={"requests": requests})
        except httpx.HTTPError as exc:
            raise RuntimeError(f"turso: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise RuntimeError(f"turso: HTTP {resp.status_code}")
        out = []
        for item in resp.json().get("results", [])[: len(stmts)]:
            if item.get("type") != "ok":
                raise RuntimeError(f"turso: {item.get('error')}")
            result = item["response"]["result"]
            self.rows_written += int(result.get("affected_row_count") or 0)
            out.append(result)
        return out

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        result = self._pipeline([(sql, params)])[0]
        rowid = result.get("last_insert_rowid")
        return int(rowid) if rowid else int(result.get("affected_row_count") or 0)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        batch: list[tuple[str, Sequence[Any]]] = []
        for row in rows:
            batch.append((sql, row))
            if len(batch) >= 200:
                self._pipeline(batch)
                batch = []
        if batch:
            self._pipeline(batch)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        result = self._pipeline([(sql, params)])[0]
        cols = [c.get("name") for c in result.get("cols", [])]
        return [dict(zip(cols, (_turso_value(c) for c in row))) for row in result.get("rows", [])]

    def script(self, sql: str) -> None:
        self._pipeline([(stmt, ()) for stmt in split_sql(sql)])

    def close(self) -> None:
        self._client.close()


_SINGLETON: Optional[Database] = None
_SINGLETON_LOCK = threading.Lock()
_FAILED_AT: Optional[float] = None
RETRY_AFTER_FAILURE = 60.0  # segundos sin reintentar si la base no abrió


def open_db(url: Optional[str] = None, *, migrate: bool = True) -> Database:
    """Abre la base indicada (o la de configuración) y, salvo que se pida lo
    contrario, aplica el esquema."""
    target = url or os.getenv("GT_COMPARE_DB_URL") or str(DEFAULT_PATH)
    if target.startswith(("libsql://", "https://")):
        db: Database = TursoDatabase(target, os.getenv("TURSO_AUTH_TOKEN", ""))
    else:
        db = SQLiteDatabase(target)
    if migrate:
        db.migrate()
    return db


def get_db() -> Optional[Database]:
    """Base compartida para el sitio. None si no hay historial disponible.

    En Vercel el disco local no persiste, así que sin `GT_COMPARE_DB_URL` no se
    intenta abrir nada. Localmente se usa el archivo solo si ya existe: el
    sitio no crea una base vacía por accidente.
    """
    global _SINGLETON, _FAILED_AT
    if _SINGLETON is not None:
        return _SINGLETON
    if _FAILED_AT is not None and time.monotonic() - _FAILED_AT < RETRY_AFTER_FAILURE:
        return None  # no se reintenta en cada visita si la base está caída
    with _SINGLETON_LOCK:
        if _SINGLETON is not None:
            return _SINGLETON
        url = os.getenv("GT_COMPARE_DB_URL")
        if not url:
            if os.getenv("VERCEL") or not DEFAULT_PATH.exists():
                return None
            url = str(DEFAULT_PATH)
        try:
            # El sitio solo lee y escribe filas: el esquema lo crea la ingesta.
            # Migrar en cada arranque en frío serían ~20 viajes a Turso.
            _SINGLETON = open_db(url, migrate=False)
        except Exception:  # noqa: BLE001
            _FAILED_AT = time.monotonic()
            return None
        return _SINGLETON

