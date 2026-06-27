# gt-compare

CLI en Python que compara precios de productos en tiempo real desde las
tiendas de ecommerce guatemaltecas que corren sobre **VTEX** (API pública de
catálogo, sin autenticación).

## Tiendas soportadas

| Key       | Tienda            | Dominio                | Estado |
|-----------|-------------------|------------------------|--------|
| `cemaco`     | Cemaco            | www.cemaco.com         | ✅ VTEX |
| `walmart`    | Walmart Guatemala | www.walmart.com.gt     | ✅ VTEX |
| `siman`      | Siman             | gt.siman.com           | ✅ VTEX |
| `max`        | Max Distelsa      | www.max.com.gt         | ✅ Constructor.io (API) |
| `curacao`    | La Curacao        | www.lacuracaonline.com | ✅ Magento (scraper HTML) |
| `radioshack` | RadioShack        | www.radioshackla.com   | ✅ Magento (scraper HTML) |
| `kemik`      | Kemik             | www.kemik.gt           | ✅ Next.js SSR (scraper HTML) |
| `pricesmart` | PriceSmart        | www.pricesmart.com     | ✅ Bloomreach Discovery (API) |

> **8 tiendas activas.** Cada plataforma se maneja distinto:
> - **VTEX** (Cemaco, Walmart, Siman): API pública de catálogo.
> - **Magento** (La Curacao, RadioShack — Grupo Unicomer): scraping del HTML de
>   `/guatemala/search/{q}` (`scraper.fetch_magento`).
> - **Next.js SSR** (Kemik): scraping del HTML de `/search?query={q}`
>   (`scraper.fetch_kemik`).
> - **Bloomreach Discovery** (PriceSmart): API pública `core.dxpapi.com` con el
>   `fl` correcto (`scraper.fetch_pricesmart`).
> - **Constructor.io** (Max): API pública usada por `/search?q=...`
>   (`scraper.fetch_max_constructor`).

## Instalación

```bash
cd gt-compare
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Uso

```bash
# Buscar en todas las tiendas
gt-compare search "taladro dewalt"

# Buscar en una tienda específica
gt-compare search "samsung 65" --store cemaco

# Buscar desde un archivo (un producto por línea)
gt-compare batch -f lista.txt

# Mostrar errores en pantalla
gt-compare search "televisor samsung" --verbose
```

La salida es una tabla ordenada de menor a mayor precio. El **precio más bajo
se resalta en verde**. En `batch`, al final se muestra el ahorro total
comprando cada ítem en la tienda más barata.

## Configuración

Al primer uso se genera `~/.gt-compare/config.yaml`. Ahí puedes deshabilitar o
agregar tiendas:

```yaml
stores:
  - key: cemaco
    name: Cemaco
    domain: www.cemaco.com
    kind: vtex
    enabled: true   # ponlo en false para deshabilitar
```

## Detalles técnicos

- **Async/paralelo**: todas las tiendas se consultan a la vez con `httpx`.
- **Timeout** de 8s por tienda; si una falla o no responde se muestra como
  *"No disponible"* y el resto continúa.
- **Cache** de 30 min en `~/.gt-compare/cache/` (archivos JSON con timestamp)
  para no martillar las APIs en búsquedas repetidas.
- **Codificación del query**: el WAF de varias tiendas VTEX rechaza el espacio
  codificado como `+` (`"Bad Request! Scripts are not allowed!"`). Como httpx
  codifica los espacios de los query params como `+`, el `ft` se construye a
  mano con `%20`, que sí es aceptado.
- **Normalización de precios (centavos)**: el spec pedía dividir entre 100 si
  `Price > 10000`. Sin embargo, **verificado contra las APIs reales**,
  `commertialOffer.Price` ya viene en quetzales (un TV de 85" reporta `11499.0`
  = Q11,499.00), así que el umbral dividiría productos legítimamente caros. Por
  eso la normalización está **desactivada** por defecto
  (`NORMALIZE_CENTS = False` en `vtex.py`); actívala solo si aparece una tienda
  que realmente devuelva centavos. Los precios `≤ 0` se ignoran al elegir el
  más barato.
- **Fallback de búsqueda**: si una búsqueda devuelve 0 resultados, se reintenta
  con solo la primera palabra del query.
- **Logs**: los errores se escriben en `~/.gt-compare/errors.log` y solo se
  muestran en pantalla con `--verbose`.

## Bot-detection / tiendas bloqueadas

Las APIs VTEX son públicas, pero algunos dominios pueden estar detrás de un WAF
(Cloudflare/Akamai) que rechaza requests automatizados con **HTTP 403/429**.
El cliente ya envía un `User-Agent` de navegador para mitigarlo.

**Estado verificado (junio 2026):**

- **Max Distelsa** (`www.max.com.gt`): los endpoints VTEX viejos siguen sin ser
  la ruta correcta para esta app, pero la búsqueda pública actual corre por
  Constructor.io y devuelve precio, stock, imagen y URL. Implementado en
  `scraper.fetch_max_constructor` y habilitado por defecto.

### PriceSmart — resuelta (Bloomreach Discovery)

PriceSmart (Nuxt) usa **Bloomreach Discovery** para búsqueda Y precios. El precio
es público en GT sin login. La trampa: el campo genérico `price` siempre es
`0.0`; el precio real está en campos **por país+club** con sufijo
`{view_id}_{club}`, p.ej. `price_GT_6303`, e **`fractionDigits=2` ⇒ viene en
centavos** (dividir entre 100). No se necesita commercetools ni token de sesión.

Parámetros (reversados del payload Nuxt y del `Copy as cURL` de la petición real):
- endpoint: `https://core.dxpapi.com/api/v1/core/`
- `account_id=7024`, `auth_key=ev7libhybjg5h1d1`
- `domain_key=pricesmart_bloomreach_io_es`, `view_id=GT`
- club por defecto `6303` (de la cookie `vsf-selected-club`); otros clubes
  pueden tener precios distintos. Configurable en `_PS_CLUB` (`scraper.py`).
- `fl` debe pedir explícitamente `price_GT_6303`, `inventory_GT_6303`, etc.

Implementado y verificado en `scraper.fetch_pricesmart`.
- **La Curacao** — **resuelto.** El dominio `www.lacuracao.com.gt` es NXDOMAIN
  y `www.lacuracao.com` redirige a icuracao.com (EE.UU.), pero la operación de
  Guatemala vive en **`www.lacuracaonline.com/guatemala/`** sobre **Magento**
  (no VTEX). GraphQL está deshabilitado (403), así que se scrapea el HTML del
  listado de búsqueda (`/guatemala/search/{query}`), extrayendo nombre, URL,
  imagen y el precio `finalPrice` (en quetzales). Implementado en
  `scraper.fetch_curacao` y **habilitada por defecto**.

Si otra tienda empieza a bloquear de forma persistente:

1. Coméntala en `gt_compare/stores.py` (lista `DEFAULT_STORES`) o ponla con
   `enabled: false` en `config.yaml`.
2. Workarounds posibles: rotar User-Agent, agregar headers de navegador
   adicionales (`Referer`, `Accept-Language: es-GT`), reducir frecuencia
   apoyándose en la cache, o usar un proxy residencial GT.

## Roadmap

- `scraper.py` aloja las tiendas **no-VTEX**, reusando los mismos objetos
  `Product`, cache y display: **La Curacao** y **RadioShack** (Magento),
  **Kemik** (Next.js SSR) y **PriceSmart** (Bloomreach Discovery). El siguiente
  candidato es **Novex** con el mismo patrón.
