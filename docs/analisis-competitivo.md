# Análisis competitivo: comparadores y sitios de ofertas, aplicado a Guatemala

Este documento revisa siete referencias internacionales (comparadores de precio, sitios de ofertas, extensiones de cupones y grupos de "price errors"), separa sus mecánicas de retención de sus modelos de ingreso y evalúa cuáles funcionan en Guatemala, donde las tiendas no tienen programas de afiliados. Al final se audita gt-compare contra esa lista.

Convención de fuentes:

- Las cifras con enlace vienen de la fuente citada en la investigación.
- "(verificado)" indica que la cifra se revisó por segunda vez.
- "(NO CONFIRMADO)" indica que no hay fuente primaria, que la fuente es de terceros o especulativa, o que la revisión no la encontró.
- La sección final lista todo lo no confirmado y dónde se buscó.

## Resumen

- **Las mecánicas que retienen usuarios no dependen de afiliados.** El historial de precio con gráfica, el aviso de "mínimo histórico", las alertas de precio objetivo, las páginas públicas de mayores bajas y la oferta del día funcionan con datos propios. Son el núcleo de Keepa, CamelCamelCamel, PriceRunner y PriceSpy, y en Guatemala se pueden copiar tal cual. La rama `feat/historial-ofertas` ya tiene la mayoría en borrador.
- **Lo que depende de comisiones queda fuera.** El cashback, los puntos (Honey Gold, los créditos de Capital One Shopping) y las recompensas en dinero por referidos se pagan con la comisión de afiliado. Sin afiliados no hay de dónde financiarlos. Los cupones que se aplican solos solo funcionan de forma adaptada.
- **Hay antecedentes claros de ingresos sin afiliados**, todos de pago directo:
  - Dealnews vende un "Sponsored Deal" a $499 que no exige ser el precio más bajo (verificado).
  - PriceRunner cobra por clic y ordena por precio, no por quién paga (verificado).
  - Prisjakt cobraba entre 1.35 y 9 SEK por clic según categoría (cifras de 2015).
  - Keepa vende una suscripción de €29/mes (verificado, solo en fuentes secundarias).
  - Brickseek cobra $19.99 y $39.99 al mes (verificado).
  - Los grupos de price errors cobran alrededor de $75/mes (verificado para priceerrors.io).
- **En Guatemala lo más directo es el patrocinio etiquetado y el cobro por clic saliente.** Hasta esta rama gt-compare no marcaba los clics hacia las tiendas; ahora todos los enlaces salientes llevan `utm_source=compa-ai&utm_medium=referral`, así la tienda ve el tráfico en su propia analítica. Falta un contador propio de clics.
- **La confianza es el activo principal.** Honey perdió cerca del 30% de sus usuarios de Chrome (de ~20M a ~14M para julio de 2025) después del escándalo de "cookie stuffing" (verificado). Las reglas que se desprenden son dos: el orden de los resultados nunca se vende, y todo lo pagado lleva la etiqueta "Patrocinado".
- **Los price errors son el diferenciador de gt-compare, pero tienen riesgo legal sin revisar.** En EE.UU. la tienda puede cancelar el pedido, y los grupos pagados lo advierten: priceerrors.io declara pagos finales y "resultados nunca garantizados" (verificado). No se revisó la base legal en Guatemala.
- **La cobertura (13 tiendas) es buena, pero faltan tiendas fáciles de integrar.** Las cinco primeras recomendadas son Elektra, Click, iShop, Almacenes Japón y Tropigas.
- **El cuello de botella técnico es el matching entre tiendas.** El EAN aparece completo en Walmart, en parte en Siman, Cemaco y Max, y casi nunca en el resto. Por eso el modelo de PriceRunner, que empareja por GTIN/EAN, no se puede copiar directo. Sobre la presencia de Google Shopping en Guatemala no hay confirmación, así que la tesis del producto no debería apoyarse en ella.

## Referencias

### Keepa y CamelCamelCamel

**Mecánica de retención o viralidad**

- Keepa muestra una gráfica de historial dentro de la página de producto de Amazon mediante una extensión de Chrome con ~4M usuarios ([Chrome Web Store](https://chromewebstore.google.com/detail/keepa-amazon-price-tracke/neebplgakaahbhdphmkckjjcegoiijjo?hl=en), [goaura.com](https://goaura.com/blog/keepa-chrome-extension)).
  - Tiene alertas configurables por condición, vendedor o porcentaje de caída, que llegan por email, push, tweet o RSS ([harpa.ai](https://harpa.ai/blog/best-price-tracker-browser-extensions)).
  - La gráfica marca el mínimo histórico.
  - El plan gratuito no vence y sirve de gancho antes del pago ([revenuegeeks.com](https://revenuegeeks.com/software/keepa/pricing)).
- CamelCamelCamel tiene la extensión "Camelizer" para Chrome, Edge, Firefox, Opera y Safari, y alertas por email.
  - Las alertas por Twitter dejaron de funcionar por la política anti-bots de X (NO CONFIRMADO: solo se vio un resultado de búsqueda de blog.kurttomlinson.com).
  - Publica páginas abiertas y compartibles sin cuenta: [destacados](https://www.camelcamelcamel.com/highlights), [populares](https://camelcamelcamel.com/popular) y [mayores bajas](https://camelcamelcamel.com/top_drops). También ofrece feeds RSS por producto y por lista, que usan bots y newsletters de terceros.

**Cómo monetiza**

- **Keepa**
  - Suscripción de consumidor: €29/mes o €290/año (verificado, solo con fuentes secundarias: [saasworthy.com](https://www.saasworthy.com/product/keepa-dev/pricing), [revenuegeeks.com](https://revenuegeeks.com/software/keepa/pricing)).
  - API B2B por tokens sin capa gratuita: planes desde €49/mes (NO CONFIRMADO: la página primaria [keepa.com/api-docs/plans-tokens.html](https://keepa.com/api-docs/plans-tokens.html) no muestra precios). Los niveles intermedios de €459 y €2,499 y el tope de €11,099/mes vienen de la misma investigación, con la misma limitación (NO CONFIRMADO).
- **CamelCamelCamel**
  - Es gratis para el usuario y vive de Amazon Associates y de anuncios ([cleartheshelf.com](https://cleartheshelf.com/camelcamelcamel-review/)).
  - La cifra de "~$200k/mes" en ingresos es una estimación de un blog de terceros (NO CONFIRMADO).
  - Fue fundada en 2008 y su mantenimiento cuesta ~$11,000/mes (verificado, [Wikipedia](https://en.wikipedia.org/wiki/Camelcamelcamel)).
  - Tiene una página de donaciones.

**¿Aplica en Guatemala sin afiliados?**

- Gráfica de historial y aviso de mínimo histórico: **sí**. Son datos propios.
- Alertas: **sí, adaptadas**. El canal de más alcance en Guatemala es WhatsApp, no email ni X.
- Páginas públicas de bajas y populares: **sí**. Generan contenido para WhatsApp y SEO sin costo.
- API o datos B2B: **sí**. Es la ruta que no depende de comisión por venta; la inteligencia de precios se vende a marcas, distribuidores o medios.
- Suscripción de consumidor: **adaptada**. Hay que poner precio en quetzales y validar que alguien pague; probablemente sea un nicho de revendedores e importadores.
- Extensión de navegador: **adaptada**. Tendría que funcionar en 13 sitios distintos en lugar de uno.
- RSS: **no**, o como mucho baja prioridad.
- Afiliados de Amazon: **no**.

### Slickdeals y Dealnews

**Mecánica de retención o viralidad**

- **Slickdeals** combina comunidad y equipo editorial.
  - Cualquier usuario publica una oferta y los demás votan con pulgar arriba o abajo. Los votos la suben de "Deal Talk" a "Popular Deals" y luego a la portada ([Slickdeals Help](https://help.slickdeals.net/hc/en-us/articles/360000551734-How-Do-I-Vote-on-a-Deal-)).
  - Un equipo de "Deal Editors", reclutados de la misma comunidad, valida antes de pasar a portada ([How Slickdeals Works](https://slickdeals.net/corp/how-slickdeals-works/)).
  - Los usuarios reportan ofertas vencidas y un equipo de calidad las baja ([Report an Expired Deal](https://help.slickdeals.net/hc/en-us/articles/22959932182171-How-Do-I-Report-an-Expired-Deal)).
  - Hay alertas por palabra clave ([Set a Deal Alert](https://daily.slickdeals.net/tech/set-a-deal-alert-on-slickdeals/)), foros, newsletter, redes sociales y una app con 600k usuarios móviles semanales ([Wikipedia](https://en.wikipedia.org/wiki/Slickdeals)).
- **Dealnews** es editorial, sin votos públicos.
  - El equipo revisa a mano cada oferta y solo publica el precio más bajo de un comercio confiable ([DealNews FAQ](https://corp.dealnews.com/faq/)).
  - La etiqueta "Editors' Choice" requiere el acuerdo de 2/3 de los editores.
  - El ranking se recalcula cada 15 minutos según el interés real de los lectores (clics y vistas).
  - Tiene app, newsletter y redes (NO CONFIRMADO el detalle de las cuentas).

**Cómo monetiza**

- **Slickdeals**
  - Comisión de afiliado del 3 al 15% del valor de venta, display y "retail media": posiciones patrocinadas vendidas con datos propios de audiencia ([AdExchanger](https://www.adexchanger.com/publishers/slickdeals-first-party-data-is-powering-direct-retail-media-sales/)).
  - Hearst y un fondo la compraron en 2018 por ~$500M. Tiene más de 12M usuarios activos mensuales y apuesta por retail media (verificado, [LA Business Journal](https://labusinessjournal.com/featured/slickdeals/)).
  - Reporta 157 empleados en 2022 y $1.53B en ventas anuales generadas para anunciantes ([Slickdeals Advertising](https://sales.slickdeals.net/)).
- **Dealnews** combina afiliados con venta directa de espacio, y la tarifa es pública ([DealNews Advertising](https://advertise.dealnews.com/)):

  | Producto | Precio | Condiciones |
  |---|---|---|
  | One Day Placement | $149/mes (verificado) | Exige "Best of Web" (precio más bajo verificado). Promedio de ~500 clics y 50 ventas. |
  | Extended Placement | $399 (verificado) | Carrusel dedicado. También exige el mejor precio. |
  | Sponsored Deal | $499 (verificado) | Posición fija con 5x impresiones. No exige ser el precio más bajo. |

  No hay cifras públicas de ingresos. El dato de "+10M páginas vistas mensuales" solo aparece en resultados de búsqueda (NO CONFIRMADO).

**¿Aplica en Guatemala sin afiliados?**

- Curación editorial con cola de aprobación: **sí**. Encaja con la cola del admin que ya existe en la rama.
- Reporte comunitario de ofertas vencidas: **sí**. Es barato y protege la confianza.
- Alertas por palabra clave: **sí**.
- Votos comunitarios: **adaptada**. El mecanismo es barato, pero con poca comunidad los votos se manipulan fácil. Conviene empezar editorial y abrir votos después.
- Sponsored Deal vendido directo a la tienda: **sí**, y es el modelo más replicable. La tienda paga por visibilidad, no por venta. El riesgo es que Dealnews no especifica cómo lo divulga al lector; en gt-compare la etiqueta "Patrocinado" tiene que ser obligatoria.
- Retail media con segmentos de audiencia: **adaptada**, y más adelante. Requiere volumen de tráfico que todavía no existe.
- Foros: **no por ahora**. El costo de moderación es alto para el tamaño del mercado.

### Brickseek

**Mecánica de retención o viralidad**

- Freemium con adelanto diario: el plan gratuito da búsqueda de inventario local y 24 ofertas seleccionadas al día (verificado, [brickseek.com/pricing](https://brickseek.com/pricing)).
- Los planes pagos agregan alertas y acceso inmediato a todas las ofertas.
- El inventario se consulta por SKU o UPC más código postal, tienda por tienda ([how-brickseek-works](https://brickseek.com/how-brickseek-works)).
- No se encontró programa de referidos (NO CONFIRMADO).

**Cómo monetiza** ([brickseek.com/pricing](https://brickseek.com/pricing))

| Plan | Precio | Qué incluye |
|---|---|---|
| Basic | Gratis (verificado) | 24 ofertas al día |
| Premium | $19.99/mes (verificado) | 15 alertas, 50 artículos en lista, 12 tiendas locales por cadena. Antes costaba $14.99. |
| Extreme | $39.99/mes (verificado) | Historial de precios y 100 alertas. Antes costaba $29.99. |

- El plan anual regala 2 meses ([blog](https://brickseek.com/blog/annual-plan)).
- No hay evidencia de que los afiliados sean la fuente principal de ingreso.
- No se publican miembros ni ingresos (NO CONFIRMADO).

**¿Aplica en Guatemala sin afiliados?**

- Inventario por tienda física: **no**. Las tiendas guatemaltecas no exponen inventario por sucursal.
- Freemium con N ofertas gratis al día y alertas o acceso completo pagados: **adaptada**. El mecanismo encaja con un nivel pago de acceso anticipado, pero no hay datos sobre cuánto pagaría el público guatemalteco.

### Grupos de price errors por suscripción (tipo Glitchndealz)

**Mecánica de retención o viralidad**

- Operan en Discord o Telegram privados, con monitores propios que avisan de errores antes que el público ([priceerrors.io](https://priceerrors.io/), [cook-groups.com](https://cook-groups.com/price-error-discords/)).
- Divine divide a sus miembros en subgrupos con avisos exclusivos para que no compitan por el mismo error.
- Norma común: no publicar capturas de la compra mientras el error sigue activo, porque las tiendas vigilan redes y lo corrigen más rápido si se viraliza ([blippr.com](https://blippr.com/blog/ultimate-guide-to-glitch-deals), [yeswecoupon.com](https://yeswecoupon.com/amazon-price-errors-glitch-deals-2026-how-they-work-what-to-do/)).
- El adelanto público en X, Facebook o un Discord abierto sirve solo para atraer suscriptores.
- glitchndealz.com está estacionado y en venta en GoDaddy (verificado). Quedan cuentas en Facebook y X.

**Cómo monetiza**

Viven de la cuota mensual de acceso, no de afiliados:

- Price Errors: $75/mes, pagos finales y "resultados nunca garantizados" (verificado, [priceerrors.io](https://priceerrors.io/)).
- Divine: ~$74.99/mes con 5 días de prueba ([cook-groups.com](https://cook-groups.com/price-error-discords/); NO CONFIRMADO con fuente primaria).
- Akira: $60/mes (misma fuente secundaria; NO CONFIRMADO).
- No se publican miembros ni ingresos (NO CONFIRMADO).

En EE.UU. un precio mal publicado no obliga a la tienda: puede cancelar y reembolsar ([legalclarity.org](https://legalclarity.org/do-stores-have-to-honor-price-mistakes-online/), [termsfeed.com](https://www.termsfeed.com/blog/pricing-mistakes-cancel-order/)).

**¿Aplica en Guatemala sin afiliados?**

- Suscripción por acceso anticipado: **adaptada**. En Guatemala se usa más WhatsApp que Discord. La idea es publicar adelantos en un canal de WhatsApp y poner el canal pago en Telegram, porque WhatsApp no controla el acceso por pago.
- Embargo de publicación mientras el error está vivo: **sí**. Encaja con la aprobación manual que ya existe en el admin.
- Aviso de que la tienda puede no respetar el precio: **sí, obligatorio**. Falta validar con un abogado local; la base legal en Guatemala no se revisó (NO CONFIRMADO).
- Tensión a decidir: publicar rápido hace crecer la audiencia, pero vender el aviso primero a la tienda afectada genera ingreso. Ninguna referencia hace lo segundo.

### Honey / Capital One Shopping

**Mecánica de retención o viralidad**

- **Honey** es una extensión que aparece en el checkout y prueba cupones en más de 30,000 sitios ([PayPal](https://www.paypal.com/us/money-hub/article/guide-to-using-paypal-honey), [Honey Help](https://help.joinhoney.com/article/39-what-is-the-honey-extension-and-how-do-i-get-it)). Además ofrece:
  - "Droplist", una lista de seguimiento con alertas por email.
  - Historial de precio simplificado.
  - Comparación entre revendedores en Amazon.
  - Puntos "Honey Gold", hoy PayPal Rewards.
  - Referidos: 500 puntos por amigo, hasta 100,000 puntos, que equivalen a $1,000 ([Honey referrals](https://help.joinhoney.com/article/33-can-i-refer-people-to-honey)).
- **Capital One Shopping** nació como Wikibuy, que Capital One compró en noviembre de 2018 con ~2M usuarios ([CNBC](https://www.cnbc.com/2018/11/20/capital-one-buys-startup-used-to-price-check-while-shopping-on-amazon.html)).
  - Compara vendedores, prueba cupones y avisa de bajas en una lista de seguimiento ([Wikipedia](https://en.wikipedia.org/wiki/Capital_One_Shopping)).
  - Paga bonos de $40 a $80 por referido ([Monkey Miles](https://monkeymiles.boardingarea.com/capital-one-shopping/)).

**Cómo monetiza**

- **Honey** vive de comisiones de afiliado. PayPal la compró en 2020 por ~$4B (verificado, [Wikipedia](https://en.wikipedia.org/wiki/PayPal_Honey)).
  - En diciembre de 2024 una investigación de MegaLag la acusó de "cookie stuffing": reemplazaba la cookie de afiliado de creadores por la suya aunque no aplicara ningún cupón (verificado, [Fortune](https://fortune.com/2024/12/23/honey-scam-browser-extension-mrbeast-mkbhd-linustechtips-megalag-expose-investigation-video)).
  - Hubo demandas colectivas desde el 29 de diciembre de 2024 ([Keller Rohrback](https://www.kellerrohrback.com/news/honey-affiliate-marketer-commissions-theft)).
  - Pasó de ~20M a ~14M usuarios de Chrome para julio de 2025, una pérdida cercana al 30% (verificado, [ppc.land](https://ppc.land/honey-drops-to-14-million-chrome-users-amid-ongoing-affiliate-scandal/)).
  - La misma fuente habla de ~8M usuarios perdidos hacia fines de 2025 (NO CONFIRMADO).
  - No hay ingresos anuales públicos (NO CONFIRMADO).
- **Capital One Shopping** cobra comisión de afiliado y devuelve una parte como créditos canjeables. Sirve de gancho para captar clientes de tarjeta de crédito ([Advertise Purple](https://www.advertisepurple.com/affiliate-spotlight-capital-one-shopping/), [FlexOffers](https://www.flexoffers.com/affiliate-programs/capital-one-shopping-ca-uk-us-affiliate-program/)).
  - Recibe críticas por recolectar datos personales ([Wikipedia](https://en.wikipedia.org/wiki/Capital_One_Shopping)).
  - No publica ingresos ni usuarios (NO CONFIRMADO).

**¿Aplica en Guatemala sin afiliados?**

- Comparación de precios y lista de seguimiento con alertas: **sí**. Ya es el núcleo de gt-compare.
- Cupones aplicados automáticamente: **adaptada**. Dependen de que existan códigos publicados, y según la investigación en Guatemala hay menos.
- Cashback, puntos y créditos: **no**. No hay comisión que los pague.
- Referidos con dinero: **no**. Con recompensa no monetaria, como acceso anticipado u opciones premium: **adaptada**.
- El modelo "banco que subsidia": **no**.
- Lecciones de confianza:
  - Nunca tocar la atribución de terceros.
  - Explicar cómo se ordenan los resultados.
  - Si se venden datos B2B, venderlos agregados y anónimos, nunca por usuario.

### PriceRunner / PriceSpy

**Mecánica de retención o viralidad**

- **PriceRunner** agrega las ofertas de cada producto y las ordena por precio. Pagar no mejora la posición (verificado, [FAQ comercios](https://www.pricerunner.com/info/faq-retailers)).
  - Ofrece alertas, listas e historial de precios ([features](https://www.pricerunner.com/info/pricerunner-features)).
  - Empareja productos por EAN/GTIN y MPN. Si no hay EAN, deja el campo vacío en lugar de inventarlo ([vendably.com](https://www.vendably.com/en/knowledge/pricerunner-product-feed-setup-and-field-mapping-guide/)).
  - Combina scraping con feeds de los comercios y verificación manual ([Wikipedia](https://en.wikipedia.org/wiki/PriceRunner)).
  - Entrega premios al "comercio del año" basados en reseñas.
- **PriceSpy (Prisjakt)** ofrece historial, alertas por email, reseñas y especificaciones.
  - Actualiza precios de 3 a 5 veces al día en promedio.
  - Obtiene los datos de las tiendas o por scraping ([pricespy.co.uk](https://pricespy.co.uk/information/how-we-compare-prices)).

**Cómo monetiza**

- **PriceRunner** es CPC puro. Los comercios que no pagan también aparecen. Es de Klarna desde 2022 (verificado, [Wikipedia](https://en.wikipedia.org/wiki/PriceRunner)). No publica su tarifa por clic (NO CONFIRMADO).
- **PriceSpy** deja que cada tienda "perfilada" elija entre CPC y CPA, un porcentaje del pedido (verificado, [pricespy.co.uk](https://pricespy.co.uk/information/how-we-compare-prices)).
  - Prisjakt cobraba por clic según categoría ([ehandel.se](https://www.ehandel.se/Prisjakt-hojer-klickpriserna-i-Sverge-och-Norge_5864-html)). Son cifras del 11 de agosto de 2015 y la tarifa actual no se conoce:

    | Segmento | Ejemplos | Precio por clic |
    |---|---|---|
    | A | Electrónica | 1.35 SEK |
    | C | Carriolas, bicicletas, camas, bombas de calor | 3.75 SEK |
    | D | Lentes de contacto | 9 SEK |

  - La versión premium, a ~49 SEK/mes, solo aparece en agregadores de terceros (NO CONFIRMADO).

**¿Aplica en Guatemala sin afiliados?**

- Ordenar por precio sin importar quién paga: **sí**. Es la regla de confianza a copiar.
- CPC por clic saliente: **adaptada**. Se negocia directo con cada tienda y requiere contar clics salientes con UTM.
- CPC diferenciado por categoría: **adaptada**. Electrónica barata por clic y nichos más caros.
- CPA a elección de la tienda: **adaptada**. Requiere que la tienda comparta conversiones, algo difícil al inicio.
- Matching por EAN: **adaptada**. En Guatemala el EAN falta en la mayoría de tiendas (ver auditoría).
- Feed directo de la tienda: **adaptada**. Empezar con scraping y pasar a feed con los socios grandes.

### Google Shopping

**Mecánica de retención o viralidad**

- Desde el 21 de abril de 2020 la pestaña Shopping pasó de anuncios pagados a listados mayormente gratuitos ([TechCrunch](https://techcrunch.com/2020/04/21/google-switches-its-shopping-search-service-to-mostly-free-listings/)).
- Las estrellas de producto requieren al menos 3 reseñas por producto y 50 en el catálogo, emparejadas por GTIN o, con peor tasa, por SKU, marca más MPN o URL ([Google 14549080](https://support.google.com/merchants/answer/14549080), [14620705](https://support.google.com/merchants/answer/14620705)).
- El feed exige ID, título, enlace, imagen, precio, disponibilidad y condición ([Google 9199328](https://support.google.com/merchants/answer/9199328)).

**Cómo monetiza**

- Hay dos programas sobre el mismo feed: listados gratuitos y Shopping Ads, pagados por clic en subasta ([mbadv.agency](https://www.mbadv.agency/google-merchant-center/difference-between-free-listings-and-paid-shopping-ads)).
- No hay un CPC único publicado porque depende de la subasta (NO CONFIRMADO).

**¿Aplica en Guatemala sin afiliados?**

- La investigación dice que Guatemala figura como país "beta" en la página de soporte de Google (NO CONFIRMADO: la revisión de la página [7101265](https://support.google.com/merchants/answer/7101265) no lo mostró).
  - Por eso no conviene afirmar que Google Shopping "no funciona" en Guatemala. La oportunidad de gt-compare se apoya mejor en otros hechos: las tiendas no publican EAN de forma consistente y nadie agrega su catálogo con historial.
- Listados gratuitos: **no**. Es un programa de Google.
- Reseñas propias ligadas a productos: **adaptada**, de impacto bajo al inicio.
- Datos estructurados de producto (JSON-LD `Product`) para aparecer en resultados de Google: **sí**. Ya se agregó en la ficha `/p/{id}`.

## Tabla de mecánicas

| Mecánica | Referencia | Aplica en GT (sí/no/adaptada) | Esfuerzo (bajo/medio/alto) | Impacto (bajo/medio/alto) |
|---|---|---|---|---|
| Historial y gráfica de precio por producto | Keepa, CamelCamelCamel, PriceRunner, PriceSpy, Honey | sí | medio | alto |
| Badge de "buen precio" o mínimo histórico | Keepa, CamelCamelCamel | sí | bajo | alto |
| Alertas de precio objetivo (WhatsApp en lugar de email o X) | Keepa, CamelCamelCamel, PriceRunner, PriceSpy, Honey Droplist, Capital One Shopping | adaptada | medio | alto |
| Alertas por palabra clave | Slickdeals | sí | medio | alto |
| Páginas públicas de mayores bajas y populares | CamelCamelCamel | sí | bajo | alto |
| Oferta del día | Dealnews (One Day), Brickseek (24 ofertas diarias gratis) | sí | bajo | alto |
| Curación editorial con cola de aprobación | Slickdeals (Deal Editors), Dealnews | sí | medio | alto |
| Reporte comunitario de oferta vencida | Slickdeals | sí | bajo | alto |
| Feed de ofertas votadas por la comunidad | Slickdeals | adaptada | medio | medio |
| Price errors con acceso anticipado pagado | priceerrors.io, Divine, Akira | adaptada | medio | alto |
| Embargo de publicación del error mientras está activo | Grupos de price errors | sí | bajo | alto |
| Freemium con N ofertas gratis al día y alertas pagadas | Brickseek | adaptada | bajo | medio |
| Suscripción de consumidor tipo Pro | Keepa | adaptada | medio | medio |
| Extensión de navegador | Keepa, CamelCamelCamel, Honey, Capital One Shopping | adaptada | alto | medio |
| Cupones aplicados automáticamente | Honey, Capital One Shopping | adaptada | medio | medio |
| Cashback o puntos | Honey Gold, créditos de Capital One Shopping | no | alto | bajo |
| Referidos con recompensa no monetaria | Honey, Capital One Shopping | adaptada | bajo | medio |
| Inventario por tienda física | Brickseek | no | alto | bajo |
| CPC por clic saliente | PriceRunner, Prisjakt | adaptada | medio | alto |
| CPC o CPA a elección de la tienda | PriceSpy | adaptada | alto | medio |
| Patrocinio de oferta etiquetado | Dealnews (Sponsored Deal) | sí | bajo | alto |
| Retail media con segmentos de audiencia | Slickdeals | adaptada | alto | medio |
| Anuncios display | CamelCamelCamel, Slickdeals | adaptada | bajo | bajo |
| Orden por precio, sin vender la posición | PriceRunner | sí | bajo | alto |
| Compartir con imagen (OG, WhatsApp, captura) | CamelCamelCamel, grupos de price errors | sí | bajo | alto |
| Newsletter | Slickdeals, Dealnews | sí | bajo | medio |
| Datos B2B o API de precios | Keepa | sí | alto | alto |
| Matching por EAN/GTIN | PriceRunner, PriceSpy, Google Shopping | adaptada | alto | alto |
| Feed directo de la tienda | PriceRunner, PriceSpy, Google Merchant Center | adaptada | alto | medio |
| Premio "tienda del año" por reseñas | PriceRunner | adaptada | medio | bajo |
| App móvil | Slickdeals, Dealnews, Honey | adaptada | alto | medio |

Notas sobre la tabla:

- "Cashback o puntos" e "Inventario por tienda física" se marcan como no aplicables. El esfuerzo y el impacto se dejan como referencia.
- La extensión tiene impacto medio porque las extensiones solo corren en navegadores de escritorio.

## Auditoría del producto actual

Estado de la rama `feat/historial-ofertas`:

- Varios módulos de la rama todavía no tienen commit: `pages.py`, `history.py`, `alerts.py`, `detector.py` y `og.py`.
- No verifiqué si algo de esto ya está en producción. "En la rama" significa que está en el árbol de trabajo.

### Contra la tabla de mecánicas

| Mecánica | Estado en gt-compare | Brecha |
|---|---|---|
| Historial y gráfica | En la rama. `history.py` guarda intervalos de precio y `/p/{id}` dibuja una gráfica SVG de 90 días. | Depende de la ingesta diaria. Los productos nuevos muestran "todavía no hay historial suficiente". |
| Badge de buen precio | En la rama (`price_badge` en `pages.py`). | Ninguna para la primera versión. |
| Alertas de precio objetivo | Parcial. El formulario de `/p/{id}` guarda la suscripción por WhatsApp. | El envío está apagado (`ALERTS_SEND_ENABLED`) y no hay proveedor de WhatsApp contratado. |
| Alertas por palabra clave | No existe. | |
| Páginas públicas de bajas | En la rama (`/ofertas`). | |
| Oferta del día | En la rama (`/oferta-del-dia`). | |
| Curación editorial | En la rama. `/admin` tiene la cola de posibles price errors por aprobar. | |
| Reporte de oferta vencida | No existe. | |
| Votos comunitarios | No existe. | Correcto dejarlo para después. |
| Price errors con acceso pagado | Parcial. Existe el detector (`detector.py`) y la aprobación manual. | No hay nivel pago ni canal privado. |
| Embargo de publicación | En la rama. Los errores solo salen al feed si el admin los aprueba. | |
| Freemium o suscripción | No existe. | |
| Extensión, cupones, cashback, referidos | No existen. | La extensión no es prioridad y el cashback no aplica. |
| CPC por clic saliente | Parcial. Desde esta rama los enlaces salientes llevan UTM (`utm_source=compa-ai`); no hay contador propio de clics. Vercel Web Analytics está instalado pero sin activar. | Con UTM la tienda puede verificar el tráfico; para cobrar CPC falta un contador propio auditable. |
| Patrocinio etiquetado | Parcial. La home reserva un espacio "Patrocinado" con el texto "Subasta abierta próximamente". | Falta vender el espacio, definir reglas de etiqueta y fijar que el patrocinio no altera el orden. |
| Orden por precio | Sí. La búsqueda pone primero lo disponible y después ordena por precio. No hay posiciones pagadas. | |
| Compartir con imagen | En la rama. `og.py` genera imágenes por producto (`/og/p/...`) y la ficha trae un botón de WhatsApp. | |
| Newsletter | No existe. | |
| Datos B2B | No existe como producto. | La base de historial es el insumo. |
| Matching por EAN | Parcial. Ver abajo. | |
| Feed directo de tienda | No existe. Todo sale de APIs públicas o scraping. | |

### Cobertura

- 13 tiendas, agrupadas por plataforma:

  | Plataforma | Tiendas |
  |---|---|
  | VTEX | Siman, Cemaco, Walmart GT |
  | Constructor.io | Max Distelsa |
  | Magento | La Curacao, RadioShack, Steren, EPA |
  | Next.js SSR | Kemik |
  | API propia | Intelaf |
  | Doofinder | Novex |
  | WooCommerce | Sears |
  | Bloomreach | PriceSmart |

- Productos que recorre la ingesta diaria por tienda:

  | Tienda | Productos |
  |---|---|
  | Kemik | ~68k declarados; se cubre ~1/3 por corrida por el límite de ritmo |
  | Walmart | ~38k declarados |
  | Cemaco | ~33k |
  | Max | ~29k |
  | Novex | ~25k |
  | Siman | ~18k |
  | EPA | ~9.7k |
  | Intelaf | ~3.9k |
  | Sears | ~3.6k |
  | PriceSmart | ~2.75k |
  | La Curacao | ~2.6k |
  | Steren | ~2–2.5k (estimado) |
  | RadioShack | ~1.1k |

- Tiendas que faltan:

  | Tienda | Plataforma | Productos | Estado de la verificación |
  |---|---|---|---|
  | Elektra | VTEX | ~1,035 | Verificada |
  | Click.gt | Shopify, `products.json` | | Verificada |
  | iShop.gt | Shopify | | Verificada |
  | Almacenes Japón | WooCommerce, API de tienda | | Verificada |
  | Paiz | VTEX | 16,654 | Verificada, pero es mayormente supermercado |
  | Tropigas | Magento | | Solo reportada por la investigación |
  | Office Depot | SAP Hybris | | Solo reportada por la investigación |
  | Tecnofácil | Next.js | | Solo reportada por la investigación |
  | La Torre | VTEX | 15,770 | Solo reportada; supermercado |
  | Samsung Store | Magento | | Solo reportada por la investigación |
  | Claro | SPA | | Solo reportada por la investigación |
  | Tigo | Respondió 403 | | Solo reportada por la investigación |

- Sin confirmar: Ekono en Guatemala, "Spirit", el dominio de Tiendas Monge y un marketplace guatemalteco.
- Las cinco primeras a agregar: Elektra, Click, iShop, Almacenes Japón y Tropigas. Cuatro usan plataformas ya soportadas o triviales (VTEX, Shopify, WooCommerce, Magento).

### Matching

- El EAN no alcanza para emparejar entre tiendas:

  | Tienda | Presencia de EAN |
  |---|---|
  | Walmart | 100% en la muestra |
  | Siman | 7/11 en una muestra de televisores |
  | Cemaco | 7/20 en una muestra de televisores |
  | Max | 25–80% según el rango de precio |
  | PriceSmart, Sears, Novex, las Magento, Kemik | ≈0 |

- El matching actual usa una regla por código de modelo con guardas de marca, tamaño, CPU y capacidad (`matching._same_product`). Jev queda para los casos ambiguos.
- La relevancia de búsqueda tiene ~60 casos de prueba.
- Comparado con PriceRunner, que empareja por GTIN, a gt-compare le falta el identificador en la mayoría de tiendas. La calidad del matching define cuántas comparaciones cruzadas se pueden mostrar, y también cuántos price errors "fuertes" (cruce entre tiendas) detecta el detector.

### Velocidad

- La búsqueda consulta en vivo las 13 tiendas, con caché CDN `s-maxage=600`.
- Línea base de Lighthouse móvil en producción, para la home:
  - LCP de 3.1 a 3.9 s, con desempeño de 66 a 82.
  - El elemento LCP es el `h1`.
  - La hoja de Google Fonts bloquea ~1 s.
  - TTFB de 73 ms.
- El problema está en el render, no en el servidor.

### Móvil

- El layout es responsive.
- Antes, el `h1` con 40px mínimo empujaba el buscador fuera de la primera pantalla. Ya se corrigió en esta rama.

### SEO

- Ya existían:
  - JSON-LD `ItemList` en `/comparar/[slug]` (11 páginas).
  - Sitemap, robots, `llms.txt` e imagen OG.
- Faltaba una ficha de producto indexable. En esta rama se agregó `/p/{id}` con JSON-LD `Product`.

### UI

- El diseño anterior usaba serif en itálica, fondo `#fdfdfc` y botones de pastilla.
- En esta rama se reemplazó por fuente del sistema, fondo blanco puro y radios de 8px.

### Riesgos y bugs encontrados

- **Riesgo:** la búsqueda en vivo de las 4 tiendas Magento usa rutas que su `robots.txt` prohíbe (`/catalogsearch`, `/guatemala/search/`). La ingesta nueva usa páginas de categoría permitidas. La búsqueda en vivo sigue usando las rutas prohibidas.
- **Bug corregido:** la búsqueda en vivo de Steren mostraba el precio de lista en lugar del precio de venta.

## Qué no pude confirmar y dónde miré

| Afirmación | Dónde se buscó | Estado |
|---|---|---|
| Precio de la API de Keepa (€49, €459, €2,499, €11,099/mes) | [keepa.com/api-docs/plans-tokens.html](https://keepa.com/api-docs/plans-tokens.html), revenuegeeks.com | La página primaria no muestra precios. |
| Suscripción de Keepa (€29/mes, €290/año) | saasworthy.com, revenuegeeks.com | Solo fuentes secundarias. |
| Keepa obtiene datos a través de los usuarios de su extensión | palant.info | Investigador externo, no Keepa. |
| Fecha de fundación de Keepa y "más de una década" de historial | Fuentes secundarias | Sin fuente primaria. |
| Clientes empresariales de Keepa (fondos, inteligencia de mercado) | Blogs de terceros | Especulativo. |
| Ingresos de CamelCamelCamel de "~$200k/mes" | Blog de terceros | Estimación especulativa. |
| Método de recolección de CamelCamelCamel (API o scraping) | Wikipedia y reseñas | No encontrado. |
| Fin de las alertas de CamelCamelCamel por Twitter/X | Resultado de búsqueda de blog.kurttomlinson.com | Página no revisada a fondo. |
| Qué tan extendidas son las quejas por alertas rotas de Slickdeals | Hilos de foro puntuales | Sin datos agregados. |
| Número de Deal Editors de Slickdeals | Páginas de ayuda y corporativas de Slickdeals | No publicado. |
| Cifras de Slickdeals (3–15% de comisión, 157 empleados, $1.53B, 600k usuarios móviles semanales) | AdExchanger, sales.slickdeals.net, Wikipedia | Citadas por la investigación. La segunda revisión solo cubrió los ~$500M, los 12M+ MAU y el retail media. |
| Ingresos de Dealnews y "+10M páginas vistas mensuales" | Resultados de búsqueda; corp.dealnews.com/faq | Sin fuente primaria. |
| Detalle de las cuentas sociales de Dealnews | Tiendas de apps | Sin página "about" con el listado. |
| Cómo divulga Dealnews el Sponsored Deal al lector | advertise.dealnews.com | La página no lo especifica. |
| Ingresos anuales de Honey | Búsqueda de reportes financieros | No públicos. |
| Pérdida de ~8M usuarios de Honey hacia fines de 2025 | ppc.land | No verificado. Solo se verificó 20M → 14M (~30%); la cifra de ~40% de la investigación se corrigió. |
| Ingresos y usuarios activos de Capital One Shopping | Búsqueda general | Unidad interna de un banco, sin reporte separado. |
| Tarifa CPC de PriceRunner | pricerunner.com/info/business, /faq-for-retailers, /getting-started | Sin cifras publicadas. |
| Premium de PriceSpy a ~49 SEK/mes | Agregadores de terceros | Sin fuente primaria. |
| CPC de Prisjakt (1.35 / 3.75 / 9 SEK) | ehandel.se | Confirmado, pero es del 11 de agosto de 2015. La tarifa actual no se conoce. |
| CPC promedio de Google Shopping Ads | Documentación de Google | Depende de la subasta. |
| Guatemala como país "beta" de Google Shopping | [support.google.com/merchants/answer/7101265](https://support.google.com/merchants/answer/7101265), merchants.glopal.com | La segunda revisión de la página de Google no lo mostró. |
| Programa de referidos y "proof-of-purchase" de Brickseek | brickseek.com/pricing, FAQ, how-it-works | El FAQ completo pide sesión. |
| Miembros e ingresos de Brickseek | Mismas páginas | No publicados. |
| Avisos de Brickseek sobre cancelaciones de la tienda | Páginas públicas | Términos completos no revisados. |
| Precios de Divine (~$74.99) y Akira ($60) | cook-groups.com | Solo fuente secundaria. |
| Programas de referidos, miembros e ingresos de los grupos de price errors | priceerrors.io, cook-groups.com | Contenido detrás del Discord pago. |
| Base legal en Guatemala para cancelar pedidos con precio erróneo | No investigado | Requiere revisión legal local. |
| Ekono operando en Guatemala | Búsqueda web | Solo se halló presencia en Costa Rica. |
| Tienda "Spirit" de electrónica u hogar | Búsqueda web | Solo aparecen Esprit (ropa) y Spirit Halloween. |
| Dominio de e-commerce de Tiendas Monge en Guatemala | Búsqueda web; grupomonge.com | No localizado. |
| Marketplace multivendedor guatemalteco con precios estructurados | Búsqueda web | No hallado. Encuentra24 son clasificados. |
| Plataforma de Tigo | curl a tigo.com.gt | Respondió 403 de Cloudflare. |
| Plataformas de Tropigas, Office Depot, Tecnofácil, La Torre, Samsung Store y Claro | curl con marcadores HTML y headers | La investigación las reportó, pero no se volvieron a verificar. |
| Conteo de productos de Tropigas, Office Depot, Tecnofácil, Samsung Store e iShop | Mismos endpoints | No obtenido. |
| "+3,000 productos" de Click | El propio sitio de Click | No verificado con conteo. |
| Catálogo de Steren (~2–2.5k) | Ingesta propia | Estimado. |
| Catálogos de Walmart (~38k) y Kemik (~68k) | APIs de cada tienda | Totales declarados por la tienda, no contados. |
| Estado en producción de las páginas nuevas (`/p/{id}`, `/ofertas`, `/oferta-del-dia`, `/admin`) | Árbol de trabajo de la rama | Presentes en la rama sin commit. No se revisó el despliegue. |
