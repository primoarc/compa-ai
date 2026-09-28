# Plan de implementación: historial, ofertas y capa de decisiones

Rama: `feat/historial-ofertas`. Stack actual: Python 3.9+ (FastAPI) servido en Vercel, HTML y JS sin framework. Este plan no cambia de stack.

## 1. Lo que ya existe y cambia el punto de partida

- **Un mes de historial ya guardado.** El cron de las 03:30 escribió 26 corridas completas de Siman, Cemaco y Walmart entre el 26-ago y el 28-sep (`~/.gt-compare/snapshots/`). Se rellenan en la base y el detector de "caída vs mediana de 30 días" funciona desde el primer día.
- **Las tiendas publican EAN.** Muestra de 20 televisores por tienda: Walmart 6/6 con EAN válido, Siman 7/11, Cemaco 7/20. Max (Constructor.io) también trae `EAN`. Intelaf, PriceSmart y Novex traen marca; Novex trae categorías. Eso vuelve determinístico buena parte del matching.
- **Límites del catálogo actual:** el barrido solo lee el primer SKU de cada producto VTEX (`items[0]`), así que variantes de color o capacidad quedan mezcladas. La ingesta nueva guarda por SKU.
- **LCP base en producción: 3.1 s y 3.9 s** (Lighthouse móvil, 4G simulado; una tercera corrida falló). El elemento LCP es el `<h1>` y la hoja de Google Fonts bloquea el render ~1 s. TTFB 73 ms: el problema es el render, no el servidor.

## 2. Arquitectura

```
                 (cron local, IP residencial)
enumeradores por tienda ──> normalizador ──> base (SQLite / Turso)
  VTEX: API de catálogo          │                │
  otros: API JSON o HTML         │                ├── productos (1 fila por SKU de tienda)
                                 │                ├── price_points (solo cambios)
                                 ▼                ├── clusters + product_clusters (matching)
                        matching y categorías     ├── deals + cola de aprobación
                        1. EAN exacto             ├── decision_cache (Jev)
                        2. reglas (códigos)       └── alert_subscriptions
                        3. Jev (ambiguos, batch)
                                 │
                                 ▼
                        detector de ofertas ──> filtro Jev (solo "posible error")
                                 │
                                 ▼
            sitio (FastAPI en Vercel): /p/{id}, /ofertas, /oferta-del-dia, /admin, OG
```

- **Ingesta fuera del request.** Todo el recorrido de catálogo, matching y categorización corre en un job batch (`python -m gt_compare.ingest`). El sitio solo lee.
- **Dónde corre el job:** en la máquina local con cron (IP residencial; las IP de datacenter de Vercel ya reciben 429 de Kemik). GitHub Actions es gratis porque el repo es público, pero sus IP son de Azure y el riesgo de bloqueo es el mismo. Queda documentado como opción, no activado.
- **Base de datos:** una capa `db` con dos backends y el mismo SQL (dialecto SQLite):
  - local: `sqlite3` de la librería estándar (`~/.gt-compare/history.db`);
  - producción: Turso vía su API HTTP con `httpx` (sin dependencias nuevas). Requiere que crees la cuenta (plan gratis). Hasta entonces, el sitio en Vercel degrada: sin historial muestra lo mismo que hoy.
- **Preview:** la vista previa local (servidor de desarrollo) corre contra la base local con datos reales. El preview de Vercel de la rama compila y degrada sin base.

## 3. Esquema de datos

```sql
runs(id INTEGER PK, store_key, started_at, finished_at, status, pages, seen, changed, errors, notes)

products(                      -- una fila por SKU de tienda
  id INTEGER PK, store_key, store_sku,          -- itemId VTEX / sku / slug
  url, name, brand, ean, model_code, image,
  store_category,                                -- ruta de la tienda
  category_id, category_source, category_conf,   -- taxonomía normalizada
  cur_price, cur_list_price, cur_available, promo_text,
  first_seen, last_seen,
  UNIQUE(store_key, store_sku))

price_points(                  -- historial: solo cuando algo cambia
  product_id, observed_at, price, list_price, available, run_id,
  PRIMARY KEY(product_id, observed_at))

clusters(id INTEGER PK, ean, name, brand, category_id)
product_clusters(product_id PK, cluster_id, method, confidence, decided_at)
match_reviews(id PK, product_a, product_b, jev_same, jev_level, status, created_at)

decision_cache(key PK, kind, value, probability, confidence, source, model, created_at)

deals(id PK, product_id, detected_at, kind, score, price, reference, features JSON,
      cause, cause_prob, cause_source, status, reviewed_at, note)
daily_pick(day PK, deal_id)
alert_subscriptions(id PK, whatsapp, product_id, target_price, consent_text, created_at, status)
```

- **Historial por cambios, no por día.** Se escribe un punto cuando cambia precio, precio de lista o disponibilidad; `last_seen` se actualiza cada corrida. Estimado: ~100k SKU, ~5% cambian por día, ~5k filas/día, ~110 MB/año. Cabe en el plan gratis de Turso.
- **Ausencia no es agotado.** Que un producto no aparezca en una corrida (páginas que fallan) no lo marca sin stock; solo el campo de disponibilidad de la tienda lo hace.

## 4. Jev: dónde entra y cómo

Documentación leída (docs.typesafe.ai, 2026-09-28): `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer`. Tres tipos de pregunta: **Noul** (probabilidad 0-1), **Choice** (hasta **255 opciones**, devuelve la elegida, probabilidades y `confidence`), **Score** (2 a 10 niveles ordenados). Varias preguntas por request, evaluadas en paralelo. Modelo `jev-1.13.0`, **US$0.042 por millón de tokens de entrada, salida gratis**, 1,200 req/min, contexto 64k. Idioma principal inglés: el español "funciona pero no igual de bien" según sus docs, así que el benchmark mide sobre títulos reales en español. Límites conocidos (página de jaggedness): no hace aritmética confiable, no compara fechas, lee literal.

**Consecuencia de diseño:** toda cuenta (razones de precio, pulgadas, GB, fechas) se hace en código y se le pasa como hecho con nombre ("el precio es ~1/100 de la mediana"), nunca como número a comparar.

**SDK:** `typesafe-sdk` 0.7.2 exige Python ≥3.10 y trae `httpx2`. El proyecto es 3.9. El adaptador llama al HTTP directo con `httpx`.

| Uso | Pregunta | Entrada (state) | Decisión en código |
|---|---|---|---|
| Matching | Score 3 niveles (distinto / variante / mismo) + Nouls (misma marca, mismo modelo, misma variante) | par de productos: nombre, marca, categoría, hechos calculados (mismas pulgadas, misma capacidad) | P(mismo) ≥ 0.85 acepta, 0.50-0.85 a revisión, < 0.50 descarta |
| Categorización | Choice nivel 1 (~15 grupos) y luego Choice nivel 2 dentro del grupo | nombre, marca, categoría de la tienda | acepta si confidence ≥ 0.7, si no queda "sin categoría" |
| Causa de price error | Choice 5 causas | hechos calculados: buckets de caída, "≈1/100", "≈1/7.6 (USD)", cambio de nombre, texto de promo con tarjeta | solo "error real" con P ≥ 0.8 entra a la cola |
| Intención de búsqueda | Choice categoría + Noul "quiere lo más barato" | la query | números (55", 128GB) por regex; timeout 400 ms, fallback a reglas |
| Oferta del día | Score de interés masivo | producto, categoría, marca | se combina en código con % de ahorro real |

**Adaptador** (`gt_compare/decide/`): `decide(schema, state) -> Decision(value, probability, confidence, source)`.
- Caché por hash (schema + state + modelo) en `decision_cache`, más LRU en memoria.
- Timeout por uso; si Jev falla, excede latencia, no hay llave o `JEV_ENABLED=0`, cae a la función determinística del esquema (y a un LLM barato solo si está configurado).
- Batch con concurrencia 6 (el cookbook oficial indica límite práctico ~8 concurrentes).
- Registra tokens para el costo real. Modelo fijado a `jev-1.13.0` porque los umbrales dependen de la versión.

**Benchmark antes de migrar** (`docs/benchmark-jev.md`):
- ≥200 pares de matching con EAN como verdad (positivos: mismo EAN en tiendas distintas; negativos difíciles: misma marca y títulos parecidos con EAN distinto).
- ≥200 productos para categorización con verdad tomada de rutas de categoría VTEX que mapean sin ambigüedad.
- Brazos: reglas actuales, Jev, LLM barato (solo si hay llave de OpenAI). Métricas: precisión y recall en aceptación, costo por 1,000 decisiones, latencia p50/p95 desde aquí.
- **Criterio de migración.** Las reglas cuestan cero, así que Jev nunca "gana en costo" contra ellas. Se interpreta así (queda en Deviations): Jev se migra para la porción que las reglas no resuelven si sube el recall sin bajar la precisión y su costo mensual proyectado es menor al del LLM que haría lo mismo. Para intención de búsqueda, la comparación directa es contra el LLM que usa hoy el planner.

## 5. Detector de ofertas

Todo en código, con fixtures:

- **Referencias:** mediana de 30 días de ese SKU (mínimo 7 observaciones), mediana de otras tiendas del mismo cluster (solo matches con confianza alta), precio de la corrida anterior, precio de lista publicado.
- **Señales:** caída vs mediana 30d, brecha vs otras tiendas, caída súbita vs corrida anterior, descuento real vs descuento anunciado.
- **Clases:** `oferta` (≥15% bajo la mediana), `oferta_fuerte` (≥30%), `posible_error` (≥50% bajo la referencia y además súbito o confirmado por otras tiendas). Solo con disponibilidad confirmada.
- **Descuento falso:** el precio de lista subió ≥20% en los 14 días antes del descuento y el precio actual no está bajo la mediana. Se marca, no se publica como oferta.
- **Filtro Jev:** solo `posible_error` pasa por la clasificación de causa; si no es "error real" con alta confianza se degrada a oferta o se descarta. Lo que pasa, va a la cola de aprobación en `/admin`.

## 6. Superficies

- `/p/{id}`: ficha con gráfica de historial (SVG generado en el servidor, sin librerías de JS), badge y comparación con otras tiendas del cluster.
- **Badge** (necesita ≥14 días y ≥7 puntos): "Buen precio" (en el 20% más bajo de su historial), "Esperá, ha estado más barato" (≥10% sobre el mínimo de 30 días), "Precio normal".
- `/ofertas` con filtros por categoría y tienda; `/oferta-del-dia` elegida en admin.
- **OG dinámico** con Pillow (1200×630 y 1080×1920), cacheado en el CDN. Botón de WhatsApp con texto de plantilla.
- **Alertas:** formulario con consentimiento que guarda WhatsApp y precio objetivo. El envío queda detrás de `ALERTS_SEND_ENABLED` (apagado).
- **Admin:** `/admin` protegido con `ADMIN_TOKEN` (comparación en tiempo constante, `noindex`, sin caché).

## 7. UI y rendimiento

- Quitar lo que tu guía prohíbe y el sitio tiene hoy: palabras en itálica en headings, fondo off-white (#fdfdfc), botones pill (radio 999px). Fondo blanco puro, radios de 8px, sin monospace.
- Móvil primero: tras buscar, el encabezado se compacta y el resultado más barato queda arriba del pliegue.
- LCP: quitar la hoja de fuentes bloqueante (fuentes auto-hospedadas con `font-display: swap` y precarga, o sistema para el texto LCP) y cachear el HTML del home en el CDN. Medir antes y después con Lighthouse.

## 8. Costos estimados (mensual)

| Rubro | Hoy | Con este plan | Nota |
|---|---|---|---|
| Vercel | US$0 (Hobby) | US$0 / US$20 | Hobby prohíbe uso comercial: al vender patrocinios hace falta Pro |
| Base de datos | - | US$0 | Turso plan gratis alcanza para ~110 MB/año |
| Jev | - | < US$1 | categorizar 100k productos una vez ≈ 80M tokens ≈ US$3.4; incremental diario centavos |
| LLM (planner) | centavos | US$0 si Jev gana | |
| WhatsApp (envío) | - | apagado | Meta cobra por conversación; se decide al activar |
| Dominio | - | ~US$1.25 | ~US$15/año |

## 9. Orden de implementación

1. Capa de base y esquema; relleno desde los snapshots existentes.
2. Ingesta VTEX por SKU con EAN, marca, categoría y texto de promo; enumeradores para las tiendas no VTEX (subagentes, uno por familia de plataforma).
3. Matching determinístico (EAN, luego códigos de modelo) y clusters.
4. Taxonomía y categorización determinística.
5. Adaptador Jev con caché, fallback y tests; armado de los sets de benchmark.
6. Detector con tests de fixtures.
7. Admin y cola de aprobación.
8. Ficha, `/ofertas`, `/oferta-del-dia`, badges, OG, WhatsApp, alertas.
9. Rediseño y LCP.
10. Documentos: análisis competitivo, monetización, benchmark (cuando haya llave).
11. Revisión del diff contra main.

## 10. Pasada de puntos ciegos

| Riesgo | Qué hago | ¿Cambia alcance? |
|---|---|---|
| Bloqueo de tiendas / IP de datacenter | concurrencia 2-4 por tienda, backoff exponencial con `Retry-After`, corte tras N errores seguidos, log por tienda, correr desde IP residencial | no |
| Términos de uso de las tiendas | solo precios públicos y enlace a la tienda; **el catálogo no se sube al repo público** ni se publica en crudo; vender datos B2B es más riesgoso que mostrarlos, queda en monetización | no |
| Costo de almacenamiento | historial por cambios (~110 MB/año). Los snapshots JSON ocupan ~470 MB por mes en tu disco: se dejan de escribir cuando la ingesta nueva reemplace al barrido; borrar los viejos es tuyo | no (borrado requiere tu permiso) |
| Variantes (color, capacidad) | historial por SKU, EAN por SKU; el matching compara SKU con SKU; los datos rellenados eran de `items[0]` y se marcan así | no |
| Precio con tarjeta vs contado | se guarda el texto de promociones de VTEX; "condición de tarjeta" es una causa en el filtro del detector; se muestra el precio general | no |
| Dependencia de Jev (early access) | modelo fijado, `JEV_ENABLED`, fallback, caché; ningún camino del producto depende de que esté arriba; límites de uso "ajustándose dinámicamente" según sus docs | no |
| Jev en español | el benchmark usa títulos reales en español; instrucciones en inglés con el state en español | no |
| Ausencia en una corrida | no cuenta como agotado | no |
| Error de moneda o unidad | hechos calculados (÷100, ÷7.6, precio por unidad vs paquete) para el filtro | no |
| Vercel Hobby no comercial | anotado en monetización; es un gasto que tenés que aprobar antes de cobrar | no |
| Datos personales (WhatsApp) | consentimiento explícito, datos mínimos, envío apagado | no |
| Publicar price errors vs vender datos a las mismas tiendas | tensión estratégica; va a monetización con una recomendación | no |
| Seguridad del admin | token por variable de entorno, sin caché, `noindex` | no |
| Definición de done pide TypeScript | el proyecto es Python; el equivalente es compilar sin errores, suites en verde y chequeo de tipos con pyright sobre los módulos nuevos | no (registrado en Deviations) |
| Zona horaria | UTC en base, `America/Guatemala` en pantalla | no |
