# Benchmark: reglas actuales contra Jev

Estado: **completo** (reglas medidas el 28-sep por mí; Jev corrido el 28-sep por el dueño desde su máquina, modelo `jev-1.13.0`).

## Qué se compara

| Uso | Solución actual | Jev | Criterio para migrar |
|---|---|---|---|
| Matching entre tiendas | Regla de `matching._same_product`: código de modelo compartido sin marca, tamaño, CPU ni capacidad contradictorios | Score de 3 niveles (distinto / variante / mismo) + 3 Nouls por campo, en una sola petición | Jev se usa solo para los pares que la regla deja en duda (mismo código, specs que chocan) si sube el recall sin bajar la precisión |
| Categorización | Palabras clave de `decide/taxonomy.py` sobre el nombre | Choice de nivel 1 (17 opciones) y después nivel 2 | Jev para lo que las reglas no ubican con palabra clara, si su exactitud aceptada (confianza ≥ 0,7) supera a la de las reglas en ese mismo grupo |
| Causa de price error | Regex de condición de pago, variante, moneda y liquidación | Choice de 6 causas | No hay set etiquetado con volumen suficiente (pocos price errors reales); se deja en reglas y el panel exige aprobación humana |
| Intención de búsqueda | Planner local (reglas) y, si hay key, gpt-5-nano | Choice de departamento + Noul "quiere lo más barato", timeout 400 ms | Solo si p95 < 400 ms desde nuestra infraestructura |
| Oferta del día | Tramos de ahorro + score estadístico | Score de 4 niveles de "atractivo para compartir" | Sin verdad de terreno: queda en reglas y la elige una persona |

Regla de costos aplicada: las reglas cuestan cero, así que Jev nunca "gana en costo"
contra ellas. Se compara contra el costo de un LLM haciendo lo mismo y se usa Jev solo
en la porción que las reglas no resuelven (ver `implementation-notes.md`, Deviations).

## Sets

Se arman con `python scripts/benchmark_jev.py build` desde el historial y se guardan
fuera del repo (`~/.gt-compare/bench/`), porque son datos de catálogo de las tiendas.

- **Matching:** pares de tiendas distintas. Positivos: mismo EAN válido (GS1, sin
  prefijos internos 2xx). Negativos difíciles: misma marca, EAN distinto y nombres
  parecidos (Jaccard de palabras ≥ 0,35). Mitad y mitad.
- **Categoría:** productos de Walmart, Siman y Cemaco cuya ruta de categoría de la
  tienda se traduce sin ambigüedad a la taxonomía propia; hasta 20 por categoría.
  Ni las reglas ni Jev ven la ruta: solo nombre y marca.

### Auditoría manual del set de matching

El EAN como verdad resultó ruidoso, así que revisé a mano los 300 pares (S = mismo
artículo vendible, V = variante de tamaño, color o paquete, D = distinto, ? = los nombres
no alcanzan para decidir). Las métricas se reportan contra las dos verdades.

| | Auditado S | Auditado V | Auditado D | ? |
|---|---|---|---|---|
| Mismo EAN (150) | 113 | 4 | 6 | 27 |
| EAN distinto (150) | 14 | 40 | 80 | 16 |

- **Emparejar solo por EAN exacto acierta el 91,9%** (113 de 123 pares decidibles).
  Los 10 errores son tiendas que reutilizan un EAN para otro tamaño o producto, por
  ejemplo Colgate Sensitive 110 g contra 75 mL, o Maybelline Volum' Express "Water Pro"
  contra "The Falsies".
- **14 de 134 pares con EAN distinto son el mismo producto**: el EAN solo no alcanza para
  encontrar todos los pares.
- Probé un chequeo de contenido declarado (ml, g, kg, oz, unidades) para frenar las fusiones
  malas por EAN: no atrapó ninguno de los 10 errores y lo descarté.

### Reglas actuales (medido el 2026-09-28, en esta máquina)

| Uso | Métrica | Contra EAN | Contra auditoría | Latencia por decisión |
|---|---|---|---|---|
| Matching (regla de código de modelo) | pares aceptados | 3 de 300 | 3 de 257 | 0,05 ms |
| | precisión | 0,667 | 0,667 | |
| | recall | 0,013 | 0,016 | |
| Categoría nivel 1 (palabras clave, solo nombre) | exactitud total | 0,45 | | 0,22 ms |
| | sin clasificar | 133 de 320 | | |
| | exactitud cuando clasifica | 0,77 | | |

Lectura:
- La regla de código de modelo sirve para electrónica y línea blanca, pero casi nunca
  aplica en supermercado, belleza, hogar o juguetes, que son la mayoría del catálogo. Hoy,
  fuera del EAN, el sistema no empareja casi nada en esas categorías. **Es el lugar donde
  Jev puede aportar más.**
- Las reglas de categoría fallan poco cuando deciden (77%), pero no deciden en 4 de cada 10
  productos.

Costo de las reglas: cero. Jev a US$0,042 por millón de tokens de entrada; un par de
matching usa ~300–400 tokens con las cuatro preguntas (estimado de los ejemplos de la
documentación; se confirma al correr), o sea ~US$0,015 por 1.000 decisiones.

### Jev (jev-1.13.0, corrido desde la máquina del dueño)

| Uso | Métrica | Reglas | Jev |
|---|---|---|---|
| Matching, contra la auditoría (257 pares) | aceptados | 3 | 45 |
| | precisión | 0,667 | **0,978** |
| | recall | 0,016 | **0,346** |
| | fusiones equivocadas | 1 | 1 |
| | a revisión humana | 0 | 50 |
| | exactitud con corte 0,5 | — | 0,837 |
| Matching, contra el EAN crudo (300 pares) | precisión / recall | 0,667 / 0,013 | 0,935 / 0,287 |
| Categoría nivel 1 (320 productos) | exactitud total | 0,45 | **0,806** |
| | sin clasificar | 133 | 6 |
| | aceptados con confianza ≥ 0,7 | — | 278 (87%) con exactitud **0,871** |
| Intención (20 consultas) | latencia p50 / p95 | — | 254 / 324 ms, 0 sobre 400 ms |

Costo y latencia de Jev medidos:

| Uso | Tokens por decisión | US$ por 1.000 decisiones | p50 | p95 | Fallos (fallback a reglas) |
|---|---|---|---|---|---|
| Matching | ~550 | 0,023 | 259 ms | 390 ms | 1 de 300 |
| Categoría | ~830 | 0,035 | 260 ms | 351 ms | 1 de 320 |

No hubo brazo de LLM (no hay `OPENAI_API_KEY` en esta máquina): no se compara contra un LLM.

### Decisión por uso

| Uso | Decisión | Por qué |
|---|---|---|
| Matching | **Migrado a Jev** para los pares que la regla no resuelve (código compartido con specs que chocan) y para candidatos nuevos por nombre (misma marca y categoría, Jaccard ≥ 0,5; ~11 k pares). También valida los pares por EAN antes de sostener una oferta fuerte o un posible error | Precisión 0,978 contra 0,667 y 20 veces más pares encontrados; costo de centavos y cache por par |
| Categorización | **Migrada a Jev** para lo que las reglas no ubican con palabra clara; 20 k productos por corrida hasta vaciar la cola (~156 k, ~US$5 en total) | 80,6% contra 45%; 87,1% cuando acepta |
| Causa de price error | Reglas | Sin set con volumen; decide una persona en el panel |
| Intención | Reglas | La latencia cumple, pero no hay set etiquetado para medir exactitud y el caso del brief ya se resolvió con reglas |
| Oferta del día | Reglas | Sin verdad de terreno |

Los usos migrados están en `WON_USES` (`gt_compare/decide/core.py`); `JEV_USES` en el entorno los
reemplaza (vacío apaga todo). Sin `TYPESAFE_API_KEY` todo cae a reglas.

Lo que el benchmark no desglosa: cuántos de los 10 pares con EAN equivocado detecta Jev. El
resultado por par no se guarda; queda como verificación para la primera corrida con Jev encendido.

## Cómo repetir el benchmark

Los sets ya están armados en `~/.gt-compare/bench/` (`match.json` incluye la auditoría
manual; no correr `build`, se niega a pisarlos sin `--force`).

```bash
cd "/Users/edwinarchila/Compa ai/gt-compare"
source .venv/bin/activate
read -rs "TYPESAFE_API_KEY?API key de TypeSafe: "; echo; export TYPESAFE_API_KEY
python scripts/benchmark_jev.py run
unset TYPESAFE_API_KEY
```

- La variable que espera es `TYPESAFE_API_KEY` (opcional: `TYPESAFE_BASE_URL`). Si no
  está en el entorno, el adaptador la busca en `gt-compare/.env.local`.
- Resultados: se imprimen en la terminal y quedan en `~/.gt-compare/bench/results.json`.
  Ese archivo es el que hay que pasarme.
- Costo estimado: ~640 peticiones × ~400 tokens ≈ 0,26 M tokens ≈ US$0,01.
- Después, encender solo los usos que ganen: variable de Actions `JEV_USES` (por ejemplo
  `match,category`) y secret `TYPESAFE_API_KEY`. En el cron local, la misma variable en
  el entorno. `match` también activa la validación de pares por EAN antes de sostener
  una oferta fuerte o un posible error.
