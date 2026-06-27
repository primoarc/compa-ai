"""Planificador opcional de búsquedas con OpenAI.

La comparación de precios sigue siendo determinística: las tiendas devuelven
productos y precios, y el filtro local decide relevancia. OpenAI solo traduce
la intención del usuario a queries alternos y reglas de inclusión/exclusión.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
from typing import Any

import httpx

from . import cache, relevance

logger = logging.getLogger("gt_compare")

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.5"
PLAN_VERSION = "query-plan-v1"
PLAN_TTL_SECONDS = int(os.getenv("GT_COMPARE_PLAN_CACHE_SECONDS", str(30 * 24 * 60 * 60)))


@dataclass
class QueryPlan:
    original_query: str
    canonical_query: str
    search_queries: list[str]
    required_any_groups: list[list[str]]
    exclude_terms: list[str]
    confidence: float = 0.0
    source: str = "local"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryPlan":
        return cls(
            original_query=str(data.get("original_query") or ""),
            canonical_query=str(data.get("canonical_query") or data.get("original_query") or ""),
            search_queries=_clean_str_list(data.get("search_queries"), limit=8),
            required_any_groups=[
                _clean_str_list(group, limit=12)
                for group in (data.get("required_any_groups") or [])
                if _clean_str_list(group, limit=12)
            ][:4],
            exclude_terms=_clean_str_list(data.get("exclude_terms"), limit=30),
            confidence=_clamp_float(data.get("confidence"), 0.0, 1.0),
            source=str(data.get("source") or "local"),
        )


def local_plan(query: str) -> QueryPlan:
    queries = relevance.search_queries(query, limit=6)
    clean = " ".join(query.strip().split())
    return QueryPlan(
        original_query=clean,
        canonical_query=clean,
        search_queries=queries or ([clean] if clean else []),
        required_any_groups=[],
        exclude_terms=[],
        confidence=0.0,
        source="local",
    )


def openai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) and not os.getenv("GT_COMPARE_DISABLE_OPENAI")


async def build_query_plan(query: str, *, use_cache: bool = True) -> QueryPlan:
    clean = " ".join(query.strip().split())
    fallback = local_plan(clean)
    if not clean or not openai_enabled():
        return fallback

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    ck = cache.make_key("openai-plan", f"{PLAN_VERSION}::{model}::{clean}")
    if use_cache:
        cached = cache.get(ck, PLAN_TTL_SECONDS)
        if isinstance(cached, dict):
            plan = QueryPlan.from_dict(cached)
            return _merge_with_fallback(clean, plan, fallback, source=plan.source or "openai-cache")

    try:
        plan = await _fetch_openai_plan(clean, model=model)
    except Exception as exc:  # noqa: BLE001 - OpenAI nunca debe tumbar búsqueda
        logger.warning("openai query planner fallback para %r: %s", clean, exc)
        return fallback

    plan = _merge_with_fallback(clean, plan, fallback, source="openai")
    if use_cache:
        cache.set(ck, plan.to_dict())
    return plan


async def _fetch_openai_plan(query: str, *, model: str) -> QueryPlan:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return local_plan(query)

    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "4"))
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"query": query}, ensure_ascii=False)},
        ],
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "retail_query_plan",
                "strict": True,
                "schema": _PLAN_SCHEMA,
            },
        },
        "max_output_tokens": 600,
        "prompt_cache_key": "gt-compare-query-planner-v1",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(OPENAI_RESPONSES_URL, headers=headers, json=payload)
        resp.raise_for_status()
    raw = _extract_output_text(resp.json())
    if not raw:
        raise ValueError("respuesta OpenAI sin output_text")
    data = json.loads(raw)
    data["source"] = "openai"
    return QueryPlan.from_dict(data)


def _merge_with_fallback(
    query: str,
    plan: QueryPlan,
    fallback: QueryPlan,
    *,
    source: str,
) -> QueryPlan:
    seen: set[str] = set()
    queries: list[str] = []
    for q in [query, *plan.search_queries, *fallback.search_queries]:
        clean = " ".join((q or "").strip().split())
        norm = relevance.normalize(clean)
        if clean and norm not in seen:
            seen.add(norm)
            queries.append(clean)
        if len(queries) >= 8:
            break

    canonical = plan.canonical_query or fallback.canonical_query or query
    return QueryPlan(
        original_query=query,
        canonical_query=canonical,
        search_queries=queries,
        required_any_groups=plan.required_any_groups,
        exclude_terms=plan.exclude_terms,
        confidence=plan.confidence,
        source=source,
    )


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content.get("text"), str):
                return content["text"]
    return ""


def _clean_str_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item).strip().split())
        norm = relevance.normalize(text)
        if not text or norm in seen or len(text) > 80:
            continue
        seen.add(norm)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _clamp_float(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


_SYSTEM_PROMPT = """You normalize retail product searches for Guatemala ecommerce.
Return only the structured JSON requested by the schema.

Goal:
- Convert the user's raw query into equivalent store search queries.
- Add lightweight title filtering rules so adjacent products do not win by price.

Rules:
- Keep search_queries short, likely to work on Spanish ecommerce sites.
- Put Spanish aliases first; include English only when common in product titles.
- Do not add brands, sizes, colors, genders, or models unless the user wrote them.
- required_any_groups is a list of concept groups. For each group, a product title
  should contain at least one term/phrase from the group. Use only essential
  concepts likely to appear in titles; avoid over-constraining.
- exclude_terms are adjacent products, accessories, consumables, or meanings that
  should not match the user's intent.
- For ambiguous one-word searches, be conservative and avoid aggressive excludes.

Examples:
- "plancha de pelo": search for "plancha de cabello", "plancha alisadora",
  "alisadora de cabello"; require one of cabello/alisadora/alisador; exclude ropa,
  vapor, cocina, aluminio, crema, shampoo, tratamiento.
- "ps5": search for "ps5", "playstation 5", "consola ps5"; require ps5/playstation
  and consola/console; exclude control, juego, headset, funda.
- "treats para perro": search for premios/snacks/golosinas para perro; require one
  pet treat term and one dog term; exclude gato if the query says perro.
"""


_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "canonical_query": {"type": "string"},
        "search_queries": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "required_any_groups": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {"type": "string"},
            },
        },
        "exclude_terms": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string"},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "canonical_query",
        "search_queries",
        "required_any_groups",
        "exclude_terms",
        "confidence",
    ],
}
