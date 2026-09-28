# Dónde correr la ingesta diaria

Evaluación del 2026-09-28: seguir con cron en la Mac o mover a GitHub Actions
con `schedule`. En ambos casos la base de producción es Turso.

## Recomendación

**GitHub Actions**, con dos condiciones antes de apagar el cron:

1. El job `reachability` de CI (corre en este PR) muestra que las 13 tiendas
   responden desde los servidores de GitHub. Si alguna bloquea IPs de
   datacenter, esa tienda sigue en el cron local y el resto en Actions.
2. Una corrida manual (`workflow_dispatch`) termina bien contra Turso.

Motivo: no depende de que la Mac esté encendida a las 3 a. m., es gratis en un
repo público, deja logs por corrida y ya quedó escrito (`.github/workflows/ingest.yml`).
Mientras tanto el cron local sigue corriendo contra la base local.

## Tiempo de corrida

Medido el 28-sep (primera corrida completa, con la versión anterior del lector VTEX
que recorría los cortes de a uno):

| Tienda | Minutos | Nota para la próxima corrida |
|---|---|---|
| Siman | 28,9 | cortes en paralelo (3) → estimado ~10 |
| Walmart | 21,3 | idem → ~8 |
| Cemaco | 20,8 | idem → ~8 |
| EPA | 17,8 | igual |
| Kemik | 16,2 | medido con el ritmo nuevo (2,5 s) y reintento de 404: 25 min, 0 errores, 19.158 SKUs |
| La Curacao | 7,2 | igual |
| Steren | 5,6 | solo página 1 por categoría → ~4 |
| Sears | 3,6 | igual |
| RadioShack | 3,3 | igual |
| Novex | 2,7 | cada 3 días |
| Max | 1,3 | igual |
| Intelaf | 0,8 | igual |
| PriceSmart | 0,4 | igual |
| **Total** | **~130** | **estimado ~100 + escrituras a Turso por red (~10)** |

Categorías, grupos y detector: 5 s en local; contra Turso, algunos minutos más por
latencia (no medido: no hay credenciales en esta máquina).

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

Plan gratis: 10 M filas escritas por mes. Estimado el 28-sep con la base local (241 k
productos vistos, 337 k intervalos), después de los ahorros de esta rama:

| Qué se escribe | Filas por día | De dónde sale el número |
|---|---|---|
| Historial: extender el intervalo de lo que no cambió | ~225 k | productos vistos por día (Novex 1 de cada 3 días, Kemik rota categorías) |
| Historial: cambio de precio (cerrar intervalo + abrir otro) | ~24 k | ~5% cambia por día (relleno VTEX: Cemaco 5,6%, Siman 9,6%, Walmart 0,5%) |
| Productos nuevos o con cambios | ~13,5 k | solo se escribe lo que cambió |
| Ofertas del día (`deals`) | ~3,2 k | una fila por oferta por día |
| Categoría de productos nuevos | ~1,5 k | |
| Índice de búsqueda (trigger) | ~1,5 k | nombres nuevos |
| Decisiones de Jev en cache | ~1 k | pares nuevos y validación de EAN; las de categoría ya no se guardan |
| Grupos y revisiones de matching | ~0,5 k | solo la diferencia |
| Panel (aprobaciones, motivos, oferta del día), alertas, corridas | < 0,1 k | |
| **Total** | **~270 k** | **~8,1 M por mes = ~81% del plan** |

Primer mes, además: copia de la base local (~0,9 M), cola de categorías débiles (~156 k) y
primeros pares por nombre (~11 k) → **~9,2 M = ~92%**.

Ahorros ya aplicados en esta rama: grupos y revisiones escriben solo la diferencia (antes
~10 k filas por día), las decisiones de categoría de Jev no se guardan (~310 k filas en la
cola inicial), y un producto sin cambios no se reescribe (antes ~240 k filas por día más).

**Pasa del 80%. Propuestas para bajarlo, de mayor a menor efecto:**

1. **Extender los intervalos día por medio** (recomendado): un producto sin cambios se marca
   como visto cada dos días en vez de cada día. Baja ~110 k filas por día → **~4,8 M por mes
   (~48%)**. Costo: "visto hoy" pasa a ser "visto hoy o ayer", así que un producto que la tienda
   quitó puede seguir un día más en /ofertas, y el fin del intervalo puede atrasarse un día.
   Los cambios de precio se siguen guardando el mismo día.
2. **Una fila por oferta mientras dure**, no una por día: ~90 k filas menos por mes.
3. **Plan Developer de Turso** (US$4,99 por mes, 25 M filas): servicio pago, lo decide el dueño.

Ninguna de las tres está aplicada: esperan decisión.

**Cómo se vigila:** cada comando que escribe suma sus filas en la tabla `db_usage` (por mes) e
imprime `Escrituras del mes AAAA-MM: N filas (X% del plan gratis de Turso)`. Desde el 80% la
línea empieza con `AVISO:` y aparece destacada en el resumen de la corrida en Actions. El
contador no ve las filas que escriben los triggers (índice de búsqueda, ~1,5 k por día) ni las
del sitio (alertas y panel, decenas por día): el número real está en el panel de Turso. El
límite se cambia con `TURSO_WRITE_LIMIT`.

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
