# Qué se publica como oferta

Reglas del detector (`gt_compare/detector.py`) y del feed (`gt_compare/pages.py`). Los
números salen del código; los tests están en `tests/test_detector.py`, `tests/test_pages.py`
y `tests/test_freshness.py`.

## Clases

| Clase | Cuándo | Dónde aparece |
|---|---|---|
| `oferta` | ≥15% bajo su referencia, con ≥7 días de historial propio | /ofertas |
| `oferta_fuerte` | ≥30% bajo su referencia, con ≥7 días de historial propio | /ofertas, candidata a oferta del día |
| `posible_error` | ≥50% y caída súbita o confirmada por otras tiendas | cola del panel; /ofertas solo si se aprueba |
| `mas_barato` | <7 días de historial propio y ≥15% bajo **todas** las demás tiendas del grupo | sección "Más barato que en otra tienda" de /ofertas; nunca oferta del día |
| `descuento_falso` | el "precio antes" subió ≥20% en dos semanas y el precio no bajó | solo en la base |

**Referencia** con historial suficiente: la menor entre la mediana de 30 y la de 90 días (sin
contar hoy), o la mediana de otras tiendas si es más baja.

## Historial mínimo (decisión del dueño, 2-oct)

- Una `oferta` u `oferta_fuerte` necesita al menos 7 días con precio propio de ese producto en
  esa tienda (en los últimos 90, sin contar hoy).
- Con menos, una diferencia contra otras tiendas se publica como "Más barato que en X", donde X
  es la tienda más barata de las demás del grupo. Solo se publica si **ninguna** tienda del
  grupo con precio de los últimos 2 días lo vende igual o más barato, validado o no su par.
  Nunca dice "oferta" y no puede ser oferta del día: el panel no la ofrece y el servidor
  rechaza elegirla.
- Un `posible_error` con menos de 7 días se mide solo contra su precio anterior (el intervalo
  previo, si existe): caída de 50% o más. Si no hay precio anterior, no se marca.

## Pares entre tiendas

- **Paquetes.** Un par en el que solo uno de los dos es paquete nunca se une automáticamente,
  ni por EAN (Max vende "IMPRESORA HP 210 + LAPTOP HP 15-FC0353LA" con el EAN de la laptop).
  Va a la cola de "Matches por revisar" del panel con origen `paquete`, sin pasar por Jev, y la
  validación de pares del detector lo rechaza.
  Es paquete un nombre con: `+` entre espacios seguido de una palabra ("+ LAPTOP"), `combo`,
  `kit`, `bundle` o `paquete`. No cuentan: `+` pegado ("7+ Años", "Active+", "S24+"), `+`
  seguido de números o specs ("4 +256GB", "RAM + SSD"), la marca Black + Decker y
  "kit de construcción" (los LEGO de Kemik). La regla todavía marca falsos positivos (ingredientes como "Biotina + Colágeno",
  juegos dobles como "Mario 3D World + Bowser's Fury"); por eso van a revisión y no se
  descartan. "Mismo" en el panel los vuelve a unir.
- **Jev no tiene una pregunta de paquete aparte.** Su pregunta principal ya separa
  "paquete o variante" (nivel 1) de "el mismo producto" (nivel 2); en el caso HP la usó bien
  (P(mismo) = 0,03). Una pregunta más costaría tokens en cada decisión e invalidaría el caché
  de ~10 k pares ya decididos, y la regla de código decide antes sin costo.
- **Misma tienda.** Un grupo puede tener dos publicaciones de la misma tienda (Max lista
  algunos productos dos veces con SKUs distintos). La ficha no las muestra en "En otras
  tiendas" y el detector no las usa como referencia.

## Feed

- **Sin repetidos.** /ofertas no repite un producto: mismo grupo entre tiendas, o misma tienda
  con el mismo nombre normalizado. Queda el de mayor puntaje.
- **Precio vigente.** Nada con precio visto hace más de 2 días (regla de 2 días): ni ofertas,
  ni oferta del día, ni badges, ni "En otras tiendas" en la ficha.
- **Ofertas retiradas.** Si el detector vuelve a correr el mismo día y una oferta ya no se
  sostiene (Jev rechazó el par, cambió el precio), su fila pasa a `withdrawn`, también si
  estaba aprobada.

## Revisar antes de publicar

`scripts/export_ofertas.py --simular` corre el detector con el código actual sin escribir y
exporta las primeras 30 de /ofertas y los "más barato" a `~/.gt-compare/` (fuera del repo).
Las decisiones de Jev salen del caché; un par sin decisión guardada va por regla, así que en
producción el resultado puede diferir en esos pares.
