"""Adaptador de decisiones: `decide(schema, state) -> Decision`.

Orden de resolución: cache (memoria y tabla `decision_cache`) → Jev → fallback
determinístico. Nada depende de que Jev esté arriba: sin API key, con el flag
apagado, con timeout, error o circuito abierto, se usa el fallback y la
decisión queda marcada con `source="rules"`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

import httpx

from ..db import Database

logger = logging.getLogger("gt_compare.decide")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"  # fijado: un alias "latest" cambiaría decisiones sin aviso
JEV_PRICE_PER_MTOK = 0.042  # USD por millón de tokens de entrada; la salida no se cobra
BATCH_CONCURRENCY = 6  # la documentación recomienda ~6; el endpoint limita arriba de ~8
BREAKER_FAILURES = 5
BREAKER_COOLDOWN = 120.0
MEMORY_CACHE_SIZE = 5000
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"


@dataclass(frozen=True)
class Question:
    kind: str  # "noul" | "choice" | "score"
    instructions: Union[str, dict, list]
    criteria: Union[None, dict, list] = None

    def payload(self) -> dict:
        out: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.criteria is not None:
            out["criteria"] = self.criteria
        return out


@dataclass(frozen=True)
class Schema:
    """Conjunto de preguntas que viajan en una sola petición.

    `primary` es la pregunta cuya respuesta se vuelve `Decision.value`. Cambiar
    preguntas u opciones exige subir `version` para no reutilizar cache vieja.
    """

    name: str
    version: str
    questions: dict[str, Question]
    primary: str

    def payload(self) -> dict:
        return {k: q.payload() for k, q in self.questions.items()}


@dataclass
class Decision:
    value: Any
    probability: Optional[float]
    confidence: Optional[float] = None
    source: str = "rules"  # "jev" | "rules" | "cache"
    answers: dict = field(default_factory=dict)
    input_tokens: int = 0
    latency_ms: float = 0.0

    def to_json(self) -> str:
        return json.dumps({"value": self.value, "answers": self.answers}, ensure_ascii=False)


Fallback = Callable[[Any], Decision]


def primary_decision(schema: Schema, answers: dict) -> tuple[Any, Optional[float], Optional[float]]:
    """(valor, probabilidad del valor, confianza) de la pregunta principal."""
    ans = answers[schema.primary]
    kind = ans.get("type")
    if kind == "noul":
        p = float(ans["noul"])
        return p >= 0.5, p, None
    if kind == "choice":
        choice = ans["choice"]
        return choice, float(ans["probabilities"].get(choice, 0.0)), ans.get("confidence")
    if kind == "score":
        probs = {int(k): float(v) for k, v in ans["probabilities"].items()}
        level = max(probs, key=lambda k: probs[k])
        return level, probs[level], ans.get("confidence")
    raise ValueError(f"respuesta desconocida: {kind}")


def cache_key(schema: Schema, state: Any, model: str = JEV_MODEL) -> str:
    blob = json.dumps([schema.name, schema.version, model, state], sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _read_api_key() -> Optional[str]:
    key = os.getenv("TYPESAFE_API_KEY")
    if key:
        return key.strip()
    try:
        for line in _ENV_FILE.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "TYPESAFE_API_KEY" and value.strip():
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


# Usos que ganaron el benchmark del 28-sep-2026 (docs/benchmark-jev.md).
WON_USES = {"match", "category"}


def use_enabled(use: str) -> bool:
    """¿Este uso va por Jev? `JEV_USES=match,category` lo fija; vacío apaga todo.
    Sin la variable valen los usos que ganaron el benchmark. Sin API key igual
    se usan las reglas: el Decider queda deshabilitado."""
    raw = os.getenv("JEV_USES")
    uses = WON_USES if raw is None else {u.strip() for u in raw.split(",") if u.strip()}
    return use in uses


class JevError(RuntimeError):
    pass


class JevClient:
    """Cliente HTTP mínimo. El SDK oficial pide Python >= 3.10."""

    def __init__(self, api_key: str, *, base_url: Optional[str] = None, model: str = JEV_MODEL,
                 transport: Optional[httpx.AsyncBaseTransport] = None, retries: int = 3):
        self.model = model
        self.retries = retries
        self._url = base_url or os.getenv("TYPESAFE_BASE_URL") or JEV_URL
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, transport=transport, timeout=30.0
        )
        self.input_tokens = 0
        self.requests = 0

    async def ask(self, state: Any, questions: dict, *, timeout: Optional[float] = None) -> dict:
        body = {"state": state, "model": self.model, "questions": questions}
        attempt = 0
        while True:
            self.requests += 1
            resp = await self._client.post(self._url, json=body, timeout=timeout or 30.0)
            if resp.status_code in (429, 529) and attempt < self.retries:
                attempt += 1
                await asyncio.sleep(min(20.0, 2 ** attempt + random.uniform(0, 1)))
                continue
            if resp.status_code != 200:
                raise JevError(f"jev {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            self.input_tokens += int((data.get("usage") or {}).get("input_tokens") or 0)
            return data

    async def aclose(self) -> None:
        await self._client.aclose()


class Decider:
    """Punto único de decisión. Una instancia por proceso alcanza."""

    def __init__(self, *, db: Optional[Database] = None, client: Optional[JevClient] = None,
                 enabled: Optional[bool] = None):
        self.db = db
        if client is None:
            key = _read_api_key()
            client = JevClient(key) if key else None
        self.client = client
        flag = os.getenv("JEV_ENABLED", "1").lower() not in ("0", "false", "no")
        self.enabled = (flag if enabled is None else enabled) and client is not None
        self._memory: OrderedDict[str, Decision] = OrderedDict()
        self._failures = 0
        self._open_until = 0.0
        self.stats = {"cache": 0, "jev": 0, "rules": 0, "errors": 0}

    # --- cache ---
    def _cache_get(self, key: str) -> Optional[Decision]:
        hit = self._memory.get(key)
        if hit is not None:
            self._memory.move_to_end(key)
            return replace(hit, source="cache", input_tokens=0, latency_ms=0.0)
        if self.db is None:
            return None
        row = self.db.query_one("SELECT * FROM decision_cache WHERE key=?", (key,))
        if row is None:
            return None
        data = json.loads(row["value"])
        dec = Decision(data["value"], row["probability"], row["confidence"], "cache",
                       data.get("answers") or {})
        self._remember(key, dec)
        return dec

    def _remember(self, key: str, dec: Decision) -> None:
        self._memory[key] = dec
        if len(self._memory) > MEMORY_CACHE_SIZE:
            self._memory.popitem(last=False)

    def _cache_put(self, key: str, schema: Schema, dec: Decision, persist: bool = True) -> None:
        self._remember(key, dec)
        if self.db is None or not persist:
            return
        self.db.execute(
            """INSERT OR REPLACE INTO decision_cache
               (key, kind, value, probability, confidence, source, model, input_tokens, created_at)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (key, schema.name, dec.to_json(), dec.probability, dec.confidence, dec.source,
             self.client.model if self.client else None, dec.input_tokens),
        )

    # --- circuito ---
    def _breaker_open(self) -> bool:
        return time.monotonic() < self._open_until

    def _record(self, ok: bool) -> None:
        if ok:
            self._failures = 0
            return
        self._failures += 1
        self.stats["errors"] += 1
        if self._failures >= BREAKER_FAILURES:
            self._open_until = time.monotonic() + BREAKER_COOLDOWN
            self._failures = 0
            logger.warning("jev: circuito abierto %ss", BREAKER_COOLDOWN)

    async def decide(self, schema: Schema, state: Any, fallback: Fallback, *,
                     timeout: Optional[float] = None, use_cache: bool = True,
                     persist: bool = True) -> Decision:
        """`persist=False`: la decisión no se guarda en la base (en Turso cada fila
        cuenta) cuando nunca se va a volver a preguntar lo mismo."""
        key = cache_key(schema, state, self.client.model if self.client else JEV_MODEL)
        if use_cache:
            hit = self._cache_get(key)
            if hit is not None:
                self.stats["cache"] += 1
                return hit
        if self.enabled and self.client is not None and not self._breaker_open():
            started = time.perf_counter()
            try:
                coro = self.client.ask(state, schema.payload(), timeout=timeout)
                data = await (asyncio.wait_for(coro, timeout) if timeout else coro)
                answers = data["answers"]
                value, prob, conf = primary_decision(schema, answers)
                dec = Decision(value, prob, conf, "jev", answers,
                               int((data.get("usage") or {}).get("input_tokens") or 0),
                               (time.perf_counter() - started) * 1000)
                self._record(True)
                self.stats["jev"] += 1
                if use_cache:
                    self._cache_put(key, schema, dec, persist)
                return dec
            except (asyncio.TimeoutError, httpx.HTTPError, JevError, KeyError, ValueError) as exc:
                self._record(False)
                logger.info("jev %s falló (%s): uso reglas", schema.name, type(exc).__name__)
        self.stats["rules"] += 1
        return fallback(state)

    async def decide_many(self, schema: Schema, states: Sequence[Any], fallback: Fallback, *,
                          concurrency: int = BATCH_CONCURRENCY, persist: bool = True) -> list[Decision]:
        sem = asyncio.Semaphore(concurrency)

        async def one(s: Any) -> Decision:
            async with sem:
                return await self.decide(schema, s, fallback, persist=persist)

        return list(await asyncio.gather(*(one(s) for s in states)))

    def cost_usd(self) -> float:
        tokens = self.client.input_tokens if self.client else 0
        return tokens / 1_000_000 * JEV_PRICE_PER_MTOK

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
