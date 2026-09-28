# robots.txt de las 13 tiendas

Revisado el 2026-09-28 contra las rutas exactas que usa cada lector. Método: se
descarga `https://{host}/robots.txt` y se evalúa con las reglas de RFC 9309 (grupo
`*`, porque nuestro user agent es el de un navegador y no se nombra en ningún
grupo; comodines `*` y `$`; gana la regla más larga y, a igual largo, `Allow`).
El parser de la librería estándar de Python dio resultados equivocados en Steren y
EPA (no maneja bien comodines ni precedencia), así que no se usa.

## Resultado

| Tienda | Uso | Ruta | robots.txt | Veredicto | Regla |
|---|---|---|---|---|---|
| Siman | ingesta y búsqueda (API VTEX) | `/api/catalog_system/pub/products/search` | 200 | permitido | sin regla |
| Cemaco | ingesta y búsqueda (API VTEX) | `/api/catalog_system/pub/products/search` | 200 | permitido | sin regla |
| Walmart | ingesta y búsqueda (API VTEX) | `/api/catalog_system/pub/products/search` | 200 | permitido | sin regla |
| Max | ingesta (Constructor.io) | `ac.cnstrc.com/browse/...` | 404 | permitido | sin robots.txt |
| Max | búsqueda (Constructor.io) | `ac.cnstrc.com/search/...` | 404 | permitido | sin robots.txt |
| La Curacao | ingesta (categoría) | `/guatemala/c/...?p=2` | 200 | permitido | sin regla |
| La Curacao | búsqueda en vivo **(antes)** | `/guatemala/search/?q=` | 200 | **prohibido** | `Disallow: /guatemala/search/` |
| RadioShack | ingesta (categoría) | `/guatemala/c/...?p=2` | 200 | permitido | sin regla |
| RadioShack | búsqueda en vivo **(antes)** | `/guatemala/search/?q=` | 200 | **prohibido** | `Disallow: /guatemala/search/` |
| Steren | ingesta (categoría, página 1) | `/audio/audifonos.html` | 200 | permitido | sin regla |
| Steren | ingesta (categoría, página 2+) **(antes)** | `/audio/audifonos.html?p=2` | 200 | **prohibido** | `Disallow: /*?` |
| Steren | búsqueda en vivo **(antes)** | `/catalogsearch/result/?q=` | 200 | **prohibido** | `Disallow: /catalogsearch/` |
| EPA | ingesta (categoría) | `/productos/...html?p=2` | 200 | permitido | sin regla |
| EPA | búsqueda en vivo **(antes)** | `/catalogsearch/result/?q=` | 200 | **prohibido** | `Disallow: /catalogsearch/*` |
| Kemik | ingesta (categoría) | `/audio?page=2` | 200 | permitido | sin regla |
| Kemik | búsqueda en vivo | `/search?query=` | 200 | permitido | sin regla |
| Intelaf | ingesta y búsqueda (API) | `api.intelaf.com:2053/app/api/producto/busqueda` | 200 | permitido | sin regla |
| PriceSmart | ingesta y búsqueda (Bloomreach) | `core.dxpapi.com/api/v1/core/` | 404 | permitido | sin robots.txt |
| Novex | ingesta y búsqueda (Doofinder) | `eu1-search.doofinder.com/5/search` | 200 | permitido | `Allow: /` |
| Sears | ingesta (Store API) | `/wp-json/wc/store/products` | 200 | permitido | sin regla |
| Sears | ingesta (respaldo HTML si la API se cierra) | `/page/2/?post_type=product` | 200 | permitido | sin regla |
| Sears | búsqueda en vivo (ahora) | `/wp-json/wc/store/products?search=` | 200 | permitido | sin regla |
| Sears | búsqueda en vivo **(antes)** | `/?s=...&post_type=product` | 200 | **prohibido** | `Disallow: /?s=*` |

EPA además bloquea por completo a varios crawlers de IA con nombre propio
(GPTBot, ClaudeBot, CCBot, Google-Extended, etc.). No aplica a nuestro user agent,
pero es una señal clara de la postura de la tienda.

## Cambios

- **La Curacao, RadioShack, Steren y EPA**: la búsqueda en vivo ya no consulta la
  tienda. Se responde desde el catálogo que la ingesta recorre por categorías
  (`gt_compare/catalog_search.py`, índice FTS5 sobre el nombre). El precio es el de
  la última corrida y el sitio lo etiqueta como "precio guardado hace X h".
- **Steren**: su robots.txt prohíbe cualquier URL con `?`, incluida la paginación de
  categorías. El lector solo pide la primera página de cada categoría. No hay
  sitemap (`/sitemap.xml` da 404), así que los productos que solo aparecen en la
  página 2 o siguientes quedan fuera: en la corrida del 28-sep eran 32 páginas
  extra sobre 214 categorías.
- **Sears**: la búsqueda en vivo pasó de `/?s=` a la Store API de WooCommerce
  (`/wp-json/wc/store/products?search=`), que sí está permitida y sigue en vivo.
- El lector Magento ya no cae a búsquedas amplias si el menú no da categorías (esa
  ruta era `/catalogsearch/`).

## Cómo repetir la revisión

```bash
python scripts/check_robots.py
```

Sale con código 1 si alguna ruta que usamos hoy está prohibida. Conviene correrlo
cada vez que se agregue una tienda o cambie una ruta.
