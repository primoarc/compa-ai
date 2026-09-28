"""Adaptador de decisiones con Jev simulado.  Ejecutar:  python tests/test_decide.py"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from gt_compare import db as dbmod  # noqa: E402
from gt_compare.decide import core, schemas  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


calls = []


def jev_ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    calls.append(body)
    answers = {}
    for qid, q in body["questions"].items():
        if q["type"] == "score":
            n = len(q["criteria"])
            probs = {str(i): 0.0 for i in range(n)}
            probs[str(n - 1)] = 0.9
            probs["0"] = 0.1
            answers[qid] = {"type": "score", "score": n - 1.1, "probabilities": probs,
                            "legend": {}, "confidence": 0.8}
        elif q["type"] == "choice":
            first = next(iter(q["criteria"]))
            answers[qid] = {"type": "choice", "choice": first,
                            "probabilities": {first: 0.93}, "confidence": 0.9}
        else:
            answers[qid] = {"type": "noul", "noul": 0.7}
    return httpx.Response(200, json={"model": "jev-1.13.0", "answers": answers,
                                     "usage": {"input_tokens": 300, "output_tokens": 20}})


def decider(handler, **kw):
    client = core.JevClient("k", transport=httpx.MockTransport(handler), retries=kw.pop("retries", 0))
    return core.Decider(client=client, enabled=True, **kw)


async def main():
    state = schemas.match_state({"name": "TV Samsung UN55DU7000"}, {"name": "Samsung UN55DU7000 55"})

    # Jev responde: valor de la pregunta principal, probabilidad y fuente
    d = decider(jev_ok)
    dec = await d.decide(schemas.MATCH, state, schemas.match_rules)
    check("fuente jev", dec.source, "jev")
    check("score -> nivel más probable", dec.value, 2)
    check("probabilidad del nivel", dec.probability, 0.9)
    check("P(mismo) para el umbral", schemas.p_same(dec), 0.9)
    check("resultado del matching", schemas.match_outcome(dec), "accept")
    check("modelo fijado", calls[-1]["model"], "jev-1.13.0")
    check("las 4 preguntas en una sola petición", len(calls[-1]["questions"]), 4)
    check("tokens contados", d.client.input_tokens, 300)

    # Segunda vez: cache en memoria, sin llamada
    n = len(calls)
    again = await d.decide(schemas.MATCH, state, schemas.match_rules)
    check("cache en memoria", (again.source, len(calls)), ("cache", n))

    # Cache persistente en la base
    mem = dbmod.open_db(":memory:")
    d1 = decider(jev_ok, db=mem)
    await d1.decide(schemas.MATCH, state, schemas.match_rules)
    d2 = decider(jev_ok, db=mem)
    n = len(calls)
    hit = await d2.decide(schemas.MATCH, state, schemas.match_rules)
    check("cache en la base sobrevive al proceso", (hit.source, hit.value, len(calls)), ("cache", 2, n))
    check("la cache guarda las respuestas completas", schemas.p_same(hit), 0.9)

    # Cambiar la versión del esquema invalida la cache
    k1 = core.cache_key(schemas.MATCH, state)
    other = core.Schema("match", "2", schemas.MATCH.questions, "relation")
    check("versión nueva, clave nueva", k1 != core.cache_key(other, state), True)

    # Error 500: fallback a reglas
    d = decider(lambda r: httpx.Response(500, text="boom"))
    dec = await d.decide(schemas.MATCH, state, schemas.match_rules)
    check("error -> reglas", (dec.source, dec.value), ("rules", 2))

    # 429 con reintento y después éxito
    seq = iter([httpx.Response(429), None])

    def flaky(request):
        r = next(seq)
        return r if r is not None else jev_ok(request)

    core_sleep = asyncio.sleep

    async def no_sleep(_):
        await core_sleep(0)

    core.asyncio.sleep = no_sleep  # type: ignore[assignment]
    try:
        d = decider(flaky, retries=2)
        dec = await d.decide(schemas.MATCH, state, schemas.match_rules, use_cache=False)
        check("429 reintenta", dec.source, "jev")
    finally:
        core.asyncio.sleep = core_sleep  # type: ignore[assignment]

    # Timeout: fallback sin esperar a Jev
    async def slow(request):
        await asyncio.sleep(1.0)
        return jev_ok(request)

    client = core.JevClient("k", transport=httpx.MockTransport(slow), retries=0)
    d = core.Decider(client=client, enabled=True)
    dec = await d.decide(schemas.INTENT, schemas.intent_state("tele 55 barata"), schemas.intent_rules,
                         timeout=0.05)
    check("timeout -> reglas", dec.source, "rules")
    check("reglas de intención", (dec.value, schemas.intent_sort(dec)), ("televisores_video", "price_asc"))

    # Circuito: después de 5 fallos no se llama más
    hits = []

    def down(request):
        hits.append(1)
        return httpx.Response(529)

    d = decider(down)
    for i in range(8):
        await d.decide(schemas.MATCH, schemas.match_state({"name": f"a{i}"}, {"name": "b"}), schemas.match_rules)
    check("circuito abierto corta llamadas", len(hits), core.BREAKER_FAILURES)

    # Sin API key: nunca llama
    d = core.Decider(client=None)
    dec = await d.decide(schemas.MATCH, state, schemas.match_rules)
    check("sin key -> reglas", (d.enabled, dec.source), (False, "rules"))

    # Flag apagado aunque haya cliente
    d = decider(jev_ok)
    d.enabled = False
    dec = await d.decide(schemas.MATCH, state, schemas.match_rules, use_cache=False)
    check("flag apagado -> reglas", dec.source, "rules")

    # Lote con concurrencia
    d = decider(jev_ok)
    states = [schemas.category_state(f"producto {i}") for i in range(20)]
    out = await d.decide_many(schemas.CATEGORY_L1, states, schemas.category_l1_rules)
    check("lote completo", len(out), 20)
    check("choice -> primera opción", out[0].value, "televisores_video")
    check("costo por tokens", round(d.cost_usd(), 8), round(20 * 300 / 1e6 * core.JEV_PRICE_PER_MTOK, 8))

    # Filtro de causa: solo error_real con P alta
    check("error real con P alta entra",
          schemas.cause_accepts(core.Decision("error_real", 0.85, source="jev")), True)
    check("error real con P baja no entra",
          schemas.cause_accepts(core.Decision("error_real", 0.6, source="jev")), False)
    check("otra causa no entra",
          schemas.cause_accepts(core.Decision("condicion_tarjeta", 0.99, source="jev")), False)


asyncio.run(main())

# --- usos encendidos: los que ganaron el benchmark, salvo que el entorno diga otra cosa ---
import os  # noqa: E402

os.environ.pop("JEV_USES", None)
check("por defecto: match y category", (core.use_enabled("match"), core.use_enabled("category"),
                                        core.use_enabled("intent")), (True, True, False))
os.environ["JEV_USES"] = ""
check("JEV_USES vacío apaga todo", core.use_enabled("match"), False)
os.environ["JEV_USES"] = "intent"
check("JEV_USES manda", (core.use_enabled("intent"), core.use_enabled("match")), (True, False))
os.environ.pop("JEV_USES", None)

# --- candidatos por nombre para el matching con Jev ---
from gt_compare import clusters  # noqa: E402


def row(i, store, name, brand="Casio", ean=None, cat="moda_accesorios/relojes_joyeria"):
    return {"id": i, "store_key": store, "name": name, "brand": brand, "ean": ean, "category_id": cat}


rows = [row(1, "walmart", "Reloj Casio para hombre modelo MQ-38UC-2A1"),
        row(2, "max", "Mq-38uc-2a1 reloj casio para hombre"),
        row(3, "max", "Reloj Casio para mujer LRW-200H"),
        row(4, "walmart", "Reloj Casio para hombre modelo MQ-38UC-2A1 negro"),   # misma tienda que 1
        row(5, "siman", "Reloj Casio para hombre modelo MQ-38UC-2A1", cat="hogar_muebles"),  # otra categoría
        row(6, "kemik", "Reloj Casio para hombre modelo MQ-38UC-2A1", ean="1234567890128"),
        row(7, "max", "Reloj Casio para hombre modelo MQ-38UC-2A1", ean="1234567890128")]
pairs = {(a["id"], b["id"]) for a, b in clusters.name_candidates(rows, exclude={(1, 2)})}
check("nunca la misma tienda", any({a, b} == {1, 4} for a, b in pairs), False)
check("excluye pares ya vistos", (1, 2) in pairs or (2, 1) in pairs, False)
check("otra categoría no se compara", any(5 in p for p in pairs), False)
check("mismo EAN no se paga dos veces", (6, 7) in pairs or (7, 6) in pairs, False)
check("sí propone el par parecido", (1, 6) in pairs or (6, 1) in pairs, True)

# --- categorías: las suposiciones débiles pasan a Jev; si Jev cae no se reescriben ---
from gt_compare import categorize  # noqa: E402


async def cats():
    mem = dbmod.open_db(":memory:")
    for i, name in enumerate(["Cosa rara sin palabra clave", "Otra cosa difícil"], 1):
        mem.execute("""INSERT INTO products (store_key, store_sku, url, name, first_seen, last_seen,
                       category_id, category_source) VALUES ('siman', ?, 'u', ?, 'd', 'd', 'otros', 'rules_low')""",
                    (str(i), name))
    out = await categorize.run(mem, decider(jev_ok), use_jev=True)
    check("débiles reclasificadas por Jev", out.get("jev"), 2)
    check("fuente guardada", {r["category_source"] for r in mem.query("SELECT category_source FROM products")},
          {"jev"})
    mem.execute("UPDATE products SET category_source='rules_low'")
    writes = []
    orig = mem.executemany
    mem.executemany = lambda sql, rows: (writes.append(len(list(rows))), None)[1]  # type: ignore[assignment]
    await categorize.run(mem, decider(lambda r: httpx.Response(500)), use_jev=True)
    mem.executemany = orig  # type: ignore[assignment]
    check("Jev caído: no se reescribe nada", writes, [0])


asyncio.run(cats())

if failures:
    print(f"{len(failures)} fallos:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK")
