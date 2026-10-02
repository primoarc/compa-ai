# Dónde correr la ingesta diaria

Evaluación del 2026-09-28: seguir con cron en la Mac o mover a GitHub Actions
con `schedule`. En ambos casos la base de producción es Turso.

## Recomendación

**Híbrido: GitHub Actions para 10 tiendas y la Mac (launchd) para Kemik, La Curacao y
RadioShack.** La lista vive en `gt_compare/ingest/hosts.py` (`LOCAL_STORES`); Actions corre
`--where actions` y la Mac `--where local`, sin tiendas en común (`tests/test_hosts.py`
comprueba que no se cruzan y que suman 13).

| Tienda | Desde GitHub | Desde la Mac | Dónde se ingiere |
|---|---|---|---|
| Kemik | **403** en todas las páginas (Cloudflare con IPs de datacenter) | completa | Mac |
| La Curacao | **406** de Fastly: 13,1% de lo declarado | 99,9% | Mac |
| RadioShack | **406** de Fastly: 8,8% de lo declarado | 99,9% | Mac |
| Siman, Cemaco, Walmart, Max, Steren, EPA, Intelaf, Novex, Sears, PriceSmart | sin errores | | Actions |

### Regla para La Curacao y RadioShack (decisión del dueño, 28-sep)

- 95% o más de cobertura en Actions: se quedan en Actions.
- Menos de 95% en Actions y 95% o más desde la Mac: pasan a `LOCAL_STORES` con Kemik.
- Menos de 95% en los dos lados: quedan como `partial`, con la etiqueta de datos parciales
  en el sitio, y se documenta en docs/robots.md. No se esquiva el bloqueo.

Resultado (2-oct): menos de 95% en Actions y 99,9% desde la Mac en las dos → Mac.

### Cobertura: cómo se calcula

**Fórmula nueva** (desde el 2-oct), solo sobre categorías que declararon su total:

    cobertura = fichas de producto listadas / productos declarados

- *Declarados*: la suma de "N Resultados" que muestra la página 1 de cada categoría.
- *Fichas listadas*: los productos de cada categoría, contando una vez por categoría (un
  producto que está en dos categorías cuenta dos veces, igual que en lo declarado), con o
  sin precio. No cuenta la plantilla vacía de "Comparar productos" que Unicomer repite en
  cada página (`<ol id="compare-items">`).
- *Categorías sin total*: si la página 1 de una categoría falla, no se sabe cuántos
  productos tiene. Esa categoría **no suma** ni a listados ni a declarados (no se cuenta como
  leída), la nota de la corrida dice cuántas quedaron así ("N categorías sin total declarado:
  cobertura no comprobable") y **una corrida con alguna categoría sin total no puede
  afirmar el 95%**, sea cual sea el porcentaje.

**Fórmula anterior:** productos únicos leídos / declarados. Subestima: cada categoría
declara su total contando productos que también están en otras, y los únicos los cuentan
una sola vez. Con cero errores, La Curacao daba 85%.

Hubo una versión intermedia (29-sep) que daba más de 100%: contaba la plantilla de
"Comparar productos" como una ficha sin precio por página (+134 en La Curacao).

| Corrida | La Curacao, anterior | La Curacao, nueva | RadioShack, anterior | RadioShack, nueva | Sin total |
|---|---|---|---|---|---|
| Mac, 28-sep (runs 81–82) | 2.587 / 3.042 = 85,0% | no recuperable¹ | 1.092 / 1.120 = 97,5% | no recuperable¹ | 0 y 0 |
| Mac, 29-sep (runs 105–106, versión intermedia) | 2.587 / 3.045 = 85,0% | 104,2%² | 1.093 / 1.121 = 97,5% | 104,5%² | 0 y 0 |
| Mac, 2-oct (en memoria, sin escribir en Turso) | 2.627 / 3.087 = 85,1% | 3.083 / 3.087 = **99,9%** | 1.091 / 1.118 = 97,6% | 1.117 / 1.118 = **99,9%** | 0 y 0 |
| Actions, 29-sep (run 36515620063) | 120 / 879 = 13,7% | no recuperable¹ | 96 / 518 = 18,5% | no recuperable¹ | 6 y 3 |
| Actions, 2-oct (run 37063390122) | 94 / 720 = 13,1% | 94 / 720 = **13,1%** | 24 / 272 = 8,8% | 24 / 272 = **8,8%** | 9 y 3 |

¹ Esas corridas no guardaron las fichas por categoría; por eso se repitió la medición el
2-oct con el mismo código en los dos lados. ² Con la plantilla de comparar contada; sin ella
da ~99,8% (3.173 − 134 = 3.039 de 3.045).

En Actions las dos fórmulas coinciden porque se lee tan poco que casi no hay productos en
dos categorías. El umbral sigue en 95%.

### Quién responde el 406

No hay cabecera `server` (el "server=?" del log era la cabecera ausente, no un error del
logger) y el cuerpo viene vacío. Las cabeceras son de Fastly: `x-served-by: cache-iad-…`,
`x-cache: MISS, MISS`, `cache-control: private, no-store` y una CSP en modo solo reporte. Un
reintento a los 20 s no rescató ninguna página en dos pruebas, y la primera categoría falla
justo después de la portada: no depende del ritmo.

## Tiempo de corrida

### Ingesta

Minutos por tienda. Donde hay medición en Actions se usa esa (corrida cancelada del 28-sep,
run 36492638919); si no, la de la Mac del 28-sep.

| Tienda | Minutos | Fuente |
|---|---|---|
| EPA | ~18 | Mac (en Actions quedó a medias al cancelar) |
| Cemaco | 11,1 | Actions |
| Siman | 8,8 | Actions |
| Walmart | 7,5 | Actions |
| Max | ~5,5 | Actions: 28 k de 32 k en 4,6 min antes del timeout de Turso |
| Sears | 3,6 | Mac |
| Steren | 3,2 | Actions |
| Novex | 2,7 | Mac; cada 3 días |
| Intelaf | ~1 | Mac |
| PriceSmart | ~0,5 | Mac |
| **Actions, 10 tiendas** | **~61** | |
| Kemik (Mac) | ~16–25 | 2,5 s por página y reintento de 404 |
| La Curacao (Mac) | 6,9 | medido el 2-oct: 135 páginas a 2,5 s |
| RadioShack (Mac) | 3,0 | medido el 2-oct: 54 páginas |
| **Mac, 3 tiendas** | **~26–35** | |

### Post-proceso

Medido en la corrida del 28-sep (run 36497703526), a partir de las horas del log:

| Fase | Minutos | Qué pasó ese día |
|---|---|---|
| Categorías | 39 | primera categorización de 20.565 productos atrasados (18.248 con Jev) |
| Grupos (matching) | 26,5 | 10.728 pares por nombre a Jev por primera vez, 7.260 vínculos escritos |
| Detector | 3,2 | 213.631 productos |
| Alertas | < 0,1 | envío apagado |
| **Total** | **~69** | Jev: 50.552 decisiones a ~13 por segundo |

Esa corrida fue la del atraso inicial. En un día normal entran ~350–1.700 productos nuevos
(mediana ~460, contando productos por `first_seen` del 15 al 27-sep), y las decisiones de
matching quedan en caché:

| Fase | Estimado diario | Cálculo |
|---|---|---|
| Categorías | 1–3 min | ~500 productos nuevos a ~1,9 min por 1.000 (lo medido) |
| Grupos | 8–14 min | la parte sin Jev (~13 min medidos, lectura y escritura por red) más solo los pares nuevos |
| Detector | ~3 min | igual que el medido |
| **Total post** | **12–20 min** | |

**Corrida diaria completa en Actions: ~75–80 min** (61 de ingesta + 12–20 de post), lejos del
timeout de 300. Desde esta versión cada fase imprime `post <fase> <segundos>s` y el resumen
de la corrida las muestra: la primera corrida normal reemplaza este estimado por la medición.

## Límites de GitHub Actions

Fuentes: docs.github.com (Actions limits, billing y events), consultadas el 28-sep.

| Límite | Valor | Impacto |
|---|---|---|
| Tiempo por job | 6 h | La corrida completa cabe en un solo job (~2 h) |
| Costo | Gratis en repos públicos con runners estándar | US$0 |
| Jobs concurrentes (plan Free) | 20 | No se necesitan más de 1 |
| `schedule` | Puede retrasarse en horas de carga, sobre todo al inicio de cada hora | Se programa a las 09:00 UTC; unos minutos de atraso no importan |
| Repos públicos | Los `schedule` se desactivan tras 60 días sin actividad en el repo | Un commit cada dos meses lo evita; si se apaga, se reactiva en la pestaña Actions |
| Logs | Públicos, porque el repo es público | Solo muestran URLs de tiendas y conteos; los secrets salen enmascarados |
| Retención | Artifact del log: 30 días (configurado). Logs de corrida: 90 días por defecto (NO CONFIRMADO en esta revisión) | |

Riesgo no medido: las IPs de GitHub son de datacenter (Azure). Kemik ya limita
con Cloudflare desde una IP residencial; desde Azure puede ser peor. El job de
alcance del PR lo va a mostrar.

## Dónde quedan los logs

- **Actions**: pestaña Actions → `ingesta-diaria` → cada corrida. El resumen de la
  corrida lista el estado por tienda; el log completo queda como artifact
  `ingest-log-<id>` por 30 días.
- **Base**: la tabla `runs` guarda estado, SKUs, cambios, páginas, errores y la nota de
  cobertura de cada tienda (`python -m gt_compare.ingest status`).
- **Cron local**: `~/.gt-compare/ingest.log`.

## Cómo se escribe a Turso desde Actions

- Secrets del repo `GT_COMPARE_DB_URL` (la URL `libsql://...`) y `TURSO_AUTH_TOKEN`.
  El workflow los pasa como variables de entorno; `gt_compare/db.py` usa la API HTTP
  de Turso (`POST {url}/v2/pipeline`) cuando la URL empieza con `libsql://` o `https://`.
- Sin los dos secrets el workflow termina sin hacer nada.
- Los errores de Turso se relanzan sin la URL de la base, y el workflow enmascara también el
  host solo (GitHub enmascara el secret tal cual, `libsql://...`, pero el cliente llama a
  `https://host`). Los tokens viajan en headers, que no se registran.

## Escrituras en Turso

Plan gratis: 10 M filas escritas por mes. Esquema aplicado (decisión del 28-sep): un
producto sin cambios se marca como visto **cada dos días**; los que están en `/ofertas`, en la
oferta del día o en la cola del panel se marcan **todos los días**. "Visto hoy" pasa a ser "visto
hoy o ayer" (`history.seen_since`); un cambio de precio se guarda siempre el mismo día.

Estimado con la base local (241 k productos vistos, 337 k intervalos):

| Qué se escribe | Filas por día | De dónde sale el número |
|---|---|---|
| Historial: marcar como visto lo que no cambió | ~113 k | ~225 k productos vistos por día / 2 (Novex 1 de cada 3 días, Kemik rota) |
| Historial: ídem, productos en ofertas, oferta del día y cola | ~3 k | se marcan a diario |
| Historial: ídem, tiendas que responden desde el catálogo | ~7,6 k | La Curacao, RadioShack, Steren y EPA (~15,2 k productos) se marcan a diario para que "actualizado hace X" sea exacto |
| Historial: cambio de precio (cerrar intervalo + abrir otro) | ~24 k | ~5% cambia por día (relleno VTEX: Cemaco 5,6%, Siman 9,6%, Walmart 0,5%) |
| Productos nuevos o con cambios | ~13,5 k | solo se escribe lo que cambió |
| Ofertas del día (`deals`) | ~3,2 k | una fila por oferta por día |
| Categoría de productos nuevos | ~1,5 k | |
| Índice de búsqueda (trigger) | ~1,5 k | nombres nuevos |
| Decisiones de Jev en cache | ~1 k | pares nuevos y validación de EAN; las de categoría no se guardan |
| Grupos y revisiones de matching | ~0,5 k | solo la diferencia |
| Panel (aprobaciones, motivos, oferta del día), alertas, corridas | < 0,1 k | |
| **Total** | **~169 k** | **~5,1 M por mes = ~51% del plan** |

Primer mes, además: copia de la base local (~0,9 M), cola de categorías débiles (~156 k) y
primeros pares por nombre (~11 k) → **~6,1 M = ~61%**. El marcado diario de las tiendas de
catálogo suma ~0,23 M por mes (antes el estimado era 48%).

Antes de este cambio el estimado era ~81% (~92% el primer mes). Otros ahorros ya aplicados:
grupos y revisiones escriben solo la diferencia (antes ~10 k filas por día), las decisiones de
categoría de Jev no se guardan (~310 k filas en la cola inicial) y un producto sin cambios no
se reescribe en `products`.

Quedan disponibles si hiciera falta: una fila por oferta mientras dure (~90 k filas menos por
mes) y el plan Developer de Turso (US$4,99 por mes, 25 M filas; pago, lo decide el dueño).

**Cómo se vigila:** cada comando que escribe suma sus filas en la tabla `db_usage` (por mes) e
imprime `Escrituras del mes AAAA-MM: N filas (X% del plan gratis de Turso)`. Desde el 80% la
línea empieza con `AVISO:` y aparece destacada en el resumen de la corrida en Actions. El
contador no ve las filas que escriben los triggers (índice de búsqueda, ~1,5 k por día) ni las
del sitio (alertas y panel, decenas por día): el número real está en el panel de Turso. El
límite se cambia con `TURSO_WRITE_LIMIT`.

## Compatibilidad con Turso

- **Falla del primer `copy` (28-sep):** `split_sql` cortaba el esquema en un `;` que estaba
  dentro de un comentario (`-- extiende end_day; si cambia, ...`), y Turso rechazó el fragmento
  "si cambia...". Ahora los comentarios se quitan respetando comillas y solo se corta donde
  SQLite da la sentencia por completa (`sqlite3.complete_statement`). Hay test que ejecuta cada
  fragmento del esquema por separado y compara el resultado con el esquema completo.
- **Qué quedó en la base después de la falla:** el pipeline de Turso ejecuta cada sentencia
  aunque otra falle, así que se crearon todas las tablas, índices, el índice de búsqueda y los
  triggers, menos `price_history` y su índice `ix_ph_end`. No se copió ningún dato. Simulado
  localmente: la migración corregida completa el esquema sobre ese estado y el `copy` se puede
  repetir sin borrar nada (es idempotente: la segunda pasada no escribe filas).
- **Prueba contra Turso real sin tocar la base:** `scripts/check_turso.py` corre en una sola
  conexión, sobre tablas TEMP, todo lo que usa el código (tipos, FTS5 con acentos y `rank`,
  triggers, upsert con `WHERE`, `INSERT OR REPLACE`, fechas, transacción) y lista qué objetos
  del esquema tiene la base.
- **Transacciones:** con Turso cada llamada es un pipeline aparte, así que `transaction()` no
  agrupa. Las escrituras están hechas para repararse solas en la corrida siguiente (upserts,
  `INSERT OR IGNORE`, intervalos que se recalculan); lo peor que deja una corrida cortada a la
  mitad es un producto con un día sin marcar.

## Que GitHub no desactive el schedule

GitHub desactiva los workflows programados de un repo público después de 60 días sin
actividad (docs.github.com, "Disabling and enabling a workflow"). La ingesta diaria no genera
actividad: no hace commits.

- **No se fabrican commits automáticos.** Es lo que hacían las acciones de "keepalive", y el
  repositorio más conocido de ellas (`gautamkrishnar/keepalive-workflow`) aparece hoy
  deshabilitado por GitHub por violar sus términos de servicio.
- **Mecanismo:** el primer paso de cada corrida cuenta los días desde el último commit. Desde
  el día 45 abre un issue "La ingesta diaria se desactiva el AAAA-MM-DD" (llega por correo) y
  lo destaca en el resumen de la corrida. No abre otro si ya hay uno abierto.
- **Qué hacer al recibirlo:** cualquier commit real al repo reinicia la cuenta. Si ya se
  desactivó:

  ```bash
  gh workflow enable ingest.yml -R primoarc/compa-ai
  ```

- Mientras el cron local siga activo, la ingesta no se pierde aunque Actions se desactive.

## Pasos para activarlo

Todo en una terminal (zsh). `read -s` pide los valores sin mostrarlos ni dejarlos en el
historial.

```bash
cd "/Users/edwinarchila/Compa ai/gt-compare"
source .venv/bin/activate
read -r "TURSO_URL?URL de Turso (libsql://...): "
read -rs "TURSO_TOKEN?Token de Turso: "; echo
```

0. Probar la compatibilidad con Turso (no modifica la base; sale con código 1 si algo falla):

   ```bash
   GT_COMPARE_DB_URL="$TURSO_URL" TURSO_AUTH_TOKEN="$TURSO_TOKEN" python scripts/check_turso.py
   ```

1. Copiar la base local a Turso (una vez; se puede repetir, lo que ya está se salta):

   ```bash
   GT_COMPARE_DB_URL="$TURSO_URL" TURSO_AUTH_TOKEN="$TURSO_TOKEN" python -m gt_compare.ingest copy --from ~/.gt-compare/history.db
   ```

2. Secrets para GitHub Actions:

   ```bash
   printf %s "$TURSO_URL" | gh secret set GT_COMPARE_DB_URL -R primoarc/compa-ai
   printf %s "$TURSO_TOKEN" | gh secret set TURSO_AUTH_TOKEN -R primoarc/compa-ai
   ```

3. Variables para Vercel (producción y previews):

   ```bash
   printf %s "$TURSO_URL" | vercel env add GT_COMPARE_DB_URL production --sensitive --yes
   printf %s "$TURSO_TOKEN" | vercel env add TURSO_AUTH_TOKEN production --sensitive --yes
   printf %s "$TURSO_URL" | vercel env add GT_COMPARE_DB_URL preview --sensitive --yes
   printf %s "$TURSO_TOKEN" | vercel env add TURSO_AUTH_TOKEN preview --sensitive --yes
   unset TURSO_URL TURSO_TOKEN
   ```

4. Correr el workflow a mano: pestaña Actions → `ingesta-diaria` → Run workflow
   (o `gh workflow run ingesta-diaria -R primoarc/compa-ai`, cuando el workflow ya
   esté en `main`).
5. Si terminó bien, apagar el cron local (pedírmelo o `crontab -e`).

Importante: con esta rama, la búsqueda en vivo de La Curacao, RadioShack, Steren y EPA
sale del catálogo guardado. **Hay que configurar Turso en Vercel antes de mergear**; si
no, en producción esas cuatro tiendas aparecen como "catálogo no disponible".

## Job local (launchd)

Corre las tiendas de `LOCAL_STORES` (`gt_compare/ingest/hosts.py`: Kemik, La Curacao y
RadioShack, ~35 min) contra Turso, con `scripts/ingest_local.sh`, que carga `~/.gt-compare/turso.env`
(`set -a; source; set +a`) sin imprimirlo y se detiene si falta la URL o el token (sin ellos
escribiría a la base local sin avisar). No hace post-proceso: lo corre Actions.

- **Hora: 20:00 de Guatemala.** Según el registro de energía de la Mac (`pmset -g log`,
  23 al 28-sep), a esa hora estuvo despierta todos los días; de madrugada a veces duerme
  (el 26-sep, de 01:45 a 11:52). Las 20:00 son las 02:00 UTC, así que los precios de las
  tres tiendas quedan en el mismo día UTC que el detector de Actions de las 09:00 UTC.
- **Mac dormida:** launchd corre el trabajo al despertar. **Mac apagada:** ese día se pierde
  y las tres tiendas quedan con el precio del día anterior; desde el tercer día sin corrida
  sus precios salen de /ofertas y de los badges (regla de 2 días) y la búsqueda muestra
  "actualizado hace N días".
- **Log:** `~/.gt-compare/ingest-local.log`.
- **Escrituras:** el comando suma lo suyo a `db_usage` en Turso igual que Actions: el aviso
  del 80% cuenta los dos lados.

Ninguna tienda se ingiere en los dos lados: Actions corre `--where actions` y la Mac
`--where local`, y las dos listas salen de `LOCAL_STORES` (`tests/test_hosts.py` comprueba
que no se cruzan y que entre las dos suman las 13). Una tienda nombrada a mano con `--store`
corre donde se pida: así se hacen las pruebas.

## Base local de la Mac

Deja de recibir ingesta: el cron viejo (las 13 tiendas a `~/.gt-compare/history.db`) se
reemplaza por el job de launchd, que escribe a Turso. Hoy ocupa ~210 MB y quedaría congelada
al 28-sep.

Como respaldo sirve poco congelada. Si se quiere respaldo, lo razonable es copiar Turso a la
Mac una vez por semana con el mismo comando al revés
(`python -m gt_compare.ingest copy --from "$GT_COMPARE_DB_URL" --to ~/.gt-compare/respaldo.db`):
~210 MB hoy, creciendo ~3 MB por día (~1 GB al año), y ~18 M filas leídas por copia en Turso
(el plan gratis trae 500 M por mes). No está programado: se decide aparte.

## Corrida cancelada del 28-sep (run 36492638919)

Terminó Siman, Cemaco, Walmart y Steren; Max quedó `partial` por un timeout de Turso sin
reintento (ya corregido: las escrituras se reintentan); La Curacao y RadioShack `partial` por
406; EPA quedó en `running` al cancelar. Al empezar cada corrida, las filas de `runs` que
siguen en `running` por más de 6 horas pasan a `abandoned`. La cadencia (Novex cada 3 días)
y la edad del catálogo que muestra la búsqueda solo miran corridas `ok` o `partial`, que
traen datos del día; `running` y `abandoned` no cuentan para nada. La corrida siguiente vuelve a recorrer todas las tiendas
sin duplicar filas: los productos son upserts, el historial extiende o reemplaza el
intervalo del día y las ofertas son una fila por producto y día.

## Activar el job local

Con `main` actualizado (después de mergear), desde la carpeta del repo:

1. **Primera corrida a mano, antes de programarla:** `zsh scripts/ingest_local.sh`.
   El script solo se validó en sintaxis; en esta corrida hay que ver:
   - que la primera línea sea `=== <fecha> ingesta local` y no aparezca `falta` ni `aviso:
     ... permisos 600`;
   - que ninguna línea muestre la URL de la base ni el token;
   - una línea por tienda: `kemik ok` (o `partial`) con ~13–19 k SKUs, y `curacao ok` y
     `radioshack ok` con `cobertura` de 95% o más y 0 errores;
   - al final `Escrituras del mes ...` con un número mayor al de la corrida de Actions:
     eso confirma que escribió en Turso y no en la base local;
   - `python -m gt_compare.ingest status` con el mismo entorno muestra Kemik con la hora
     de esta corrida.
2. Quitar la línea vieja del cron (las 13 tiendas a la base local). Ver antes qué hay con
   `crontab -l`, y luego `crontab -l | grep -v "gt_compare.ingest run" | crontab -`.
3. Instalar el job:
   `sed -e "s|__REPO__|$PWD|g" -e "s|__HOME__|$HOME|g" scripts/launchd/com.compa-ai.ingest-local.plist > ~/Library/LaunchAgents/com.compa-ai.ingest-local.plist`
   y `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.compa-ai.ingest-local.plist`.
4. Probar el job de launchd sin esperar a las 20:00:
   `launchctl kickstart gui/$(id -u)/com.compa-ai.ingest-local`.
5. Verificar: `tail -f ~/.gt-compare/ingest-local.log` muestra lo mismo que la corrida a mano;
   `launchctl print gui/$(id -u)/com.compa-ai.ingest-local | grep -E "state|last exit code"`
   debe decir `last exit code = 0` al terminar.

Para desactivarlo: `launchctl bootout gui/$(id -u)/com.compa-ai.ingest-local`.
