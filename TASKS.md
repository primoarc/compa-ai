# Tareas

Fuente de verdad del progreso de `feat/historial-ofertas`. Plan: `implementation-plan.md`. Desvíos: `implementation-notes.md`.

## Parte A: análisis
- [x] Investigación por referencia (Keepa/CCC, Slickdeals/Dealnews, Brickseek/grupos de price errors, Honey/Capital One, PriceRunner/PriceSpy/Google Shopping)
- [x] Verificar cifras clave contra fuente primaria
- [x] Tiendas de Guatemala que faltan
- [x] Auditoría del producto actual (cobertura, matching, velocidad, móvil, SEO, UI)
- [x] docs/analisis-competitivo.md

## Parte B: Jev
- [x] Leer docs (API, primitivas, límites, SDK, jaggedness)
- [x] Adaptador `decide()` con caché, fallback, timeout, circuito y tests
- [x] Esquemas: matching, categoría, causa de error, intención, oferta del día
- [x] Sets etiquetados: 300 pares (EAN + auditoría manual) y 320 productos
- [x] Harness de benchmark (`scripts/benchmark_jev.py`)
- [x] Brazo de reglas medido y docs/benchmark-jev.md escrito
- [x] Brazo de Jev (corrido por el dueño el 28-sep)
- [x] Migrar solo los usos que ganen: matching y categorización (`WON_USES`)

## Parte C: núcleo
- [x] Capa de base (SQLite local, Turso HTTP) y esquema
- [x] Relleno desde snapshots existentes (78 archivos, 2,2 M observaciones → 190 k intervalos)
- [x] Ingesta VTEX por SKU con EAN, marca, categoría, promo
- [x] Enumeradores no VTEX (Max, Magento x4, Kemik, Intelaf, PriceSmart, Novex, Sears)
- [x] Rate limit, backoff y logs por tienda; cadencia por tienda (Novex cada 3 días)
- [x] Corrida completa guardada en las 13 tiendas (Kemik parcial: 144 respuestas fallidas sin explicar)
- [x] Matching determinístico (EAN, códigos) y clusters; decisiones manuales del panel mandan
- [x] Taxonomía y categorización determinística
- [x] Detector (señales, clases, descuento falso) con tests de fixtures
- [x] Admin con cola de aprobación y oferta del día
- [x] Ficha de producto con gráfica y badge
- [x] /ofertas y /oferta-del-dia
- [x] OG dinámico (1200x630 y 9:16) y compartir a WhatsApp
- [x] Alertas: opt-in guardando registros, envío apagado por flag
- [x] UI: quitar itálicas, off-white y pills; móvil primero (más barato sin scroll)
- [x] LCP home antes y después
- [x] LCP /ofertas (1,05 s) y ficha (0,75 s)
- [x] Enlaces salientes con UTM

## Parte D
- [x] docs/monetizacion.md

## Ronda 2 (decisiones del 28-sep)
- [x] Comandos para correr el benchmark de Jev (docs/benchmark-jev.md)
- [x] Cron reemplazado por `python -m gt_compare.ingest run` (respaldo en ~/.gt-compare/crontab.backup-20260928)
- [x] Evaluación GitHub Actions + workflow (docs/ingesta-programada.md, .github/workflows/)
- [x] Comandos para cargar Turso en local, GitHub y Vercel
- [x] Vercel Web Analytics activado (plan Hobby, sin costo)
- [x] robots.txt de las 13 tiendas revisado (docs/robots.md, scripts/check_robots.py)
- [x] Búsqueda en vivo de Curacao, RadioShack, Steren y EPA desde el catálogo; Sears por Store API
- [x] Admin: login por POST con cookie httpOnly
- [x] Validación de pares por EAN (Jev o regla) antes de oferta fuerte o posible error
- [x] Precio de contado y de tarjeta por separado; condición en la ficha y en la búsqueda
- [x] Miniaturas desde la CDN (VTEX y Max)
- [x] Texto completo de la SIL OFL
- [x] Fixtures sintéticos
- [x] Kemik: status y URLs de las fallas
- [x] Scripts viejos borrados
- [x] Logs del workflow sin URL de base ni tokens
- [x] Estimado de escrituras en Turso + aviso del 80% en cada corrida
- [x] Aviso de inactividad de 60 días (issue desde el día 45)
- [x] Kemik verificado: 0 errores con el ritmo nuevo
- [x] Escrituras de Turso: marcado día por medio salvo ofertas, oferta del día y cola (estimado 48%)
- [x] Squash, push y PR
- [x] Falla del copy contra Turso: split_sql cortaba en un ";" dentro de un comentario (corregido, con test)
- [x] Rango de precio VTEX "1e+06" daba HTTP 400: 6 productos sobre Q20.000 no se leían (corregido)
- [x] Script de compatibilidad con Turso sin tocar la base (scripts/check_turso.py)
- [ ] Copy contra Turso (lo corre el dueño)

## Ronda 3 (La Curacao y RadioShack, 28-sep)
- [x] Primer 406 por tienda: todas las cabeceras y 300 caracteres del cuerpo; aclarar "server=?"
- [x] Curacao/RadioShack: 2,5 s, un reintento del 406 tras 20 s, seguir con la siguiente categoría
- [x] Cobertura leídos/declarados en el resultado de cada tienda
- [x] Flag de solo ingesta en workflow_dispatch
- [x] Prueba en Actions de las dos tiendas y decisión por la regla del 95% (≤14% y ≤19%: falta la prueba desde la Mac)
- [x] Prueba desde la Mac (99,9% y 99,9%) y paso de las dos a LOCAL_STORES
- [x] Precios de más de 2 días fuera del detector, /ofertas, /oferta-del-dia y badges (con test de corrida parcial)
- [x] "actualizado hace X" en resultados desde catálogo
- [x] ubuntu-24.04 en los workflows
- [x] Tiempos del post-proceso por fase y estimado de la corrida diaria
- [x] Job local de Kemik: hora según uso de la Mac, qué verificar en la primera corrida
- [x] Exportar las 30 primeras de /ofertas para revisión manual
- [x] PR listo con comandos, sin mergear

## Ronda 4 (feed antes de compartir, 2-oct)
- [x] Cobertura: quitar la plantilla de comparar, categorías sin total, fórmula y tabla en el doc
- [x] La Curacao y RadioShack a LOCAL_STORES (test de 10 + 3)
- [x] Historial mínimo de 7 días; "más barato que en X" aparte y nunca oferta del día
- [x] Paquete de un solo lado: a revisión en grupos, validación y detector (caso HP)
- [x] Sin repetidos en /ofertas (grupo o misma tienda con el mismo nombre)
- [x] Ficha sin la misma tienda en "En otras tiendas"
- [x] Origen de la aprobación del HP y lista de pares paquete contra simple
- [x] Ofertas que ya no se sostienen el mismo día: withdrawn
- [x] Una tienda que falla al escribir no frena la corrida
- [x] Export de las 30 primeras con las reglas nuevas (simulado, sin escribir)

## Cierre
- [x] Chequeo de tipos (pyright, todo el paquete) y 14 suites en verde
- [x] Revisión del diff contra main y correcciones

## Hallazgos
- LCP base en producción (home): 3.1 s y 3.9 s; elemento LCP `<h1>`; Google Fonts bloquea ~1 s.
- LCP local simulado 4G (home): 2,9 s antes, 1,06 s después.
- `typesafe-sdk` exige Python ≥3.10; el proyecto es 3.9, se llama al HTTP directo.
- El barrido viejo solo guardaba `items[0]` de cada producto VTEX: las variantes se mezclaban.
- La búsqueda en vivo de Steren mostraba el precio de lista en vez del de venta (corregido, con test).
- La búsqueda en vivo de las 4 tiendas Magento usa rutas que su robots.txt prohíbe (sin cambiar; decisión del dueño).
- Intelaf muestra como precio el de pago en efectivo ("Beneficio Efectivo"); con tarjeta es más caro.
- Novex busca con Doofinder, que probablemente le cobra cada búsqueda a la tienda.
- Kemik: los ~68 k "declarados" se cuentan doble entre categorías; el árbol completo dio 13.657 SKUs únicos en 586 páginas.
- EAN exacto acierta 91,9% en la auditoría: hay tiendas que reutilizan EAN entre tamaños o productos.
- Siman y Max publican artículos no vendibles a Q8.000.000–Q99.999.999; se filtran en la ingesta.
- La regla de código de modelo une tintas con impresoras ("GI-25"): su confianza bajó a 0,7 y ya no sirve de referencia de precio.
- Las miniaturas vienen de la tienda en tamaño completo (1.000–1.600 px) para mostrarse a 56 px.
