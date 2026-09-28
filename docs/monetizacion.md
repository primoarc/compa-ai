# Monetización de Compa AI

Evaluación de cuatro modelos para un comparador de precios en Guatemala sin
afiliados ni Google Shopping. No implementa cobros. Todas las cifras en
quetzales usan **Q7.70 por US$1**. Lo que no está confirmado dice
**(SUPUESTO)** o **(NO CONFIRMADO)** y la fuente o el razonamiento.

## 1. Costo de operación mensual

| Rubro | Hoy | Al monetizar | Fuente o cálculo |
|---|---|---|---|
| Vercel | US$0 (Hobby) | **US$20** (Pro) | vercel.com/pricing: "Our Hobby plan is for personal, non-commercial use." Pro US$20/mes. Consultado 2026-09-28. |
| Base del historial (Turso) | US$0 | US$0 | turso.tech/pricing: plan gratis 5 GB, 500 M filas leídas y 10 M escritas al mes. La base local mide 73 MB con 33 días de 3 tiendas; con 13 tiendas se estima < 1 GB el primer año. |
| Ingesta diaria | US$0 | US$0 | Corre en una máquina propia (hoy la Mac local). Alternativa sin costo: GitHub Actions en repo público (SUPUESTO: minutos gratis ilimitados para repos públicos). ~1,5 h por corrida. |
| Jev (TypeSafe) | US$0 | **< US$2** | US$0,042 por millón de tokens de entrada (docs.typesafe.ai, 2026-09-28). Categorizar el catálogo una vez: ~230 k productos × ~350 tokens ≈ 80 M tokens ≈ US$3,4 una sola vez. Diario: ~1.500 productos nuevos + pares ambiguos ≈ 0,6 M tokens ≈ US$0,03/día. Intención en tiempo real, si se enciende: 20 k búsquedas × 400 tokens ≈ US$0,34/mes. |
| LLM (planner gpt-5-nano) | centavos | US$0–3 | Solo si `OPENAI_API_KEY` está en producción (NO CONFIRMADO: no puedo leer la configuración de Vercel). |
| Dominio propio | US$0 | ~US$1,25 | ~US$15/año (SUPUESTO, precio típico .com). |
| **Total fijo** | **~US$0** | **~US$25 ≈ Q190** | |
| WhatsApp (alertas) | apagado | **variable** | Meta cobra por mensaje de plantilla según categoría y país. No encontré la tarifa oficial para Guatemala (NO CONFIRMADO; busqué en developers.facebook.com/documentation/business-messaging/whatsapp/pricing y agregadores). SUPUESTO: US$0,06–0,08 por alerta de precio si Meta la clasifica como *marketing*. 1.000 alertas/mes ≈ US$70 ≈ Q540. |

Alternativas de envío sin costo por mensaje: canal de Telegram o bot de
Telegram (gratis), y Canales de WhatsApp para difusión en un solo sentido
(gratis, pero sin alertas personalizadas). Para validar demanda conviene
empezar por ahí y dejar la API de WhatsApp para suscriptores pagados.

## 2. Supuestos de tráfico

No hay datos reales todavía: la analítica de Vercel está instalada pero sin
activar. Escenarios para dimensionar (SUPUESTO):

| Escenario | Usuarios/mes | Páginas vistas/mes | Clics salientes a tiendas/mes |
|---|---|---|---|
| Mes 3 | 5.000 | 20.000 | 7.500 |
| Mes 6 | 20.000 | 80.000 | 30.000 |
| Mes 12 | 60.000 | 240.000 | 90.000 |

Clics salientes = 1,5 por usuario (SUPUESTO; en un comparador el usuario suele
abrir una o dos tiendas por búsqueda).

## 3. Modelos

### 3.1 Suscripción premium: alertas y price errors con acceso anticipado

- **Qué se vende:** alertas por WhatsApp a precio objetivo sin límite, y los
  price errors aprobados en el panel 2–6 horas antes de que salgan en
  `/ofertas`. El modelo existe en EE. UU. (priceerrors.io US$75/mes, pagos
  finales y sin garantía de que la tienda respete el precio; Brickseek Premium
  US$19,99 y Extreme US$39,99; ver `analisis-competitivo.md`).
- **Precio propuesto:** Q29/mes (SUPUESTO; ~US$3,8, pensado para el poder de
  compra local, muy por debajo de las referencias de EE. UU.).
- **Costo variable por suscriptor:** pasarela ~5% + Q2 por cobro (SUPUESTO;
  Stripe no opera con comercios en Guatemala, NO CONFIRMADO; opciones locales
  tipo Recurrente o BAC) y ~15 alertas/mes × US$0,07 = US$1,05 ≈ Q8.
- **Margen por suscriptor:** Q29 − Q3,5 − Q8 ≈ **Q17,5**.
- **Punto de equilibrio:** Q190 / Q17,5 ≈ **11 suscriptores**.
- **Escenario mes 6:** 0,5% de 20.000 usuarios = 100 suscriptores ≈ Q1.750/mes
  de margen.
- **Riesgos:** los price errors no son constantes. En la primera corrida completa
  (28-sep-2026, 13 tiendas, 212.824 productos evaluados) el detector dejó 21 candidatos
  en la cola de aprobación, la mayoría liquidaciones profundas más que errores; todavía
  no hay un mes de historial para saber cuántos errores reales aparecen por semana; las tiendas
  cancelan pedidos; publicar errores puede tensar la relación con las mismas
  tiendas a las que después se les quiere vender (ver 3.3 y 3.4). Mitigación:
  el panel exige aprobación manual y la causa probable debe ser "error real".

### 3.2 Patrocinio y subasta del espacio existente

- **Qué se vende:** el espacio "Patrocinado" de la home (ya reservado en el
  layout) y, más adelante, una "oferta patrocinada" dentro de `/ofertas`
  marcada como tal. Referencia: Dealnews vende *Sponsored Deal* a US$499 sin
  exigir que la oferta sea la mejor de la web, y paquetes de un día a US$149.
- **Mecánica de subasta:** semanal, sobre cerrada al segundo precio con precio
  mínimo de Q500 por semana (SUPUESTO). Idea del dueño: el pago no se devuelve;
  si nadie supera la puja durante el período, el espacio queda para quien pagó.
  Hay que escribir las reglas en términos y condiciones antes de cobrar.
- **Alternativa por CPM:** 80.000 impresiones/mes × US$2 CPM (SUPUESTO; CPM
  display en Centroamérica) ≈ US$160 ≈ Q1.230/mes.
- **Punto de equilibrio:** **una semana vendida** al mínimo (Q500) cubre el
  costo fijo (Q190).
- **Riesgo:** con poco tráfico demostrable nadie paga. Necesita las métricas
  de la sección 5.

### 3.3 Inteligencia de precios B2B

- **Qué se vende:** reportes o tablero con el historial de precios por
  categoría y tienda, alertas de cambios de la competencia y detección de
  "precio antes" inflado. Clientes naturales: marcas y distribuidores
  (monitoreo de precio mínimo anunciado), más que las propias tiendas.
- **Precio propuesto:** Q1.500–4.000/mes por cliente (SUPUESTO; herramientas de
  monitoreo de precios en EE. UU. cobran del orden de US$100–400/mes, NO
  CONFIRMADO en esta sesión).
- **Punto de equilibrio:** **un cliente**.
- **Requisitos antes de vender:** 90 días de historial continuo, cobertura
  medida por tienda > 90% en la categoría que se vende, y revisión legal de los
  términos de uso de cada tienda: la ingesta usa APIs públicas y páginas de
  categoría permitidas por robots.txt, pero vender los datos es un uso distinto
  a mostrarlos.
- **Tensión:** es el modelo con mejor margen y el más sensible a que una
  tienda bloquee la ingesta. No conviene lanzarlo mientras el sitio dependa de
  la buena voluntad de esas mismas tiendas para la búsqueda en vivo.

### 3.4 Cobro por tráfico referido negociado directo (UTM)

- **Qué se vende:** clics salientes medidos con parámetros UTM, cobrados por
  clic a cada tienda. Es lo que hacen PriceRunner y PriceSpy (CPC; PriceSpy
  también ofrece CPA). Referencia de precios: Prisjakt cobraba 1,35 SEK por
  clic en electrónica y hasta 9 SEK en otros segmentos (tarifas del 11 de
  agosto de 2015).
- **Precio propuesto:** Q0,50–1,00 por clic (SUPUESTO).
- **Escenario mes 6:** 30.000 clics × 40% hacia tiendas que acepten × Q0,75 ≈
  **Q9.000/mes**.
- **Punto de equilibrio:** ~250 clics pagados al mes.
- **Cómo empezar sin cobrar:** mandar a cada tienda un reporte mensual gratis
  con los clics que les enviamos (sus propias analíticas los ven con
  `utm_source=compa-ai`), y negociar después de 2–3 meses de datos.
- **Riesgo:** en Guatemala no hay programas de afiliados; cada acuerdo es una
  negociación manual y la tienda puede no confiar en nuestra cuenta de clics.

## 4. Orden de lanzamiento

1. **Mes 0–3, gratis y midiendo.** Activar la analítica de Vercel, etiquetar
   los clics salientes con UTM, publicar `/ofertas` y la oferta del día, y un
   canal gratuito (Telegram o Canal de WhatsApp) para los price errors
   aprobados. Pasar a Vercel Pro antes del primer cobro.
2. **Patrocinio del espacio** cuando haya ≥ 10.000 usuarios/mes o ≥ 5.000
   clics salientes/mes. Es la venta más simple y una semana cubre el costo.
3. **CPC por UTM** con las 2–3 tiendas que más clics reciben, usando los
   reportes de los meses anteriores.
4. **Suscripción premium** cuando haya ≥ 500 alertas activas gratis y al menos
   un price error aprobado por semana durante un mes; recién ahí se enciende el
   envío por WhatsApp (flag `ALERTS_SEND_ENABLED`) y se contrata el proveedor.
5. **B2B** al final, con 90 días de historial y revisión legal.

## 5. Métricas antes de vender a tiendas

| Métrica | Para qué | Dónde se mide |
|---|---|---|
| Clics salientes por tienda y semana (UTM) | Base de cualquier cobro a tiendas | Analítica del sitio + analítica de la tienda |
| CTR del resultado más barato | Muestra que el usuario compra por precio | Analítica del sitio |
| Usuarios que vuelven en 30 días | Retención; los patrocinadores la piden | Analítica del sitio |
| Alertas activas y bajas | Demanda de la suscripción | Tabla `alert_subscriptions` |
| Compartidos por WhatsApp e imágenes descargadas | Motor viral | Clics en el botón, pedidos a `/og/p/*` |
| Frescura y cobertura por tienda | Calidad del dato que se vende | Tabla `runs` (`python -m gt_compare.ingest status`) |
| Price errors aprobados por semana | Si la suscripción tiene qué ofrecer | Tabla `deals` |

## 6. Resumen de puntos de equilibrio

Costo fijo al monetizar ≈ **Q190/mes**.

| Modelo | Unidad | Margen por unidad | Unidades para cubrir el costo |
|---|---|---|---|
| Suscripción | suscriptor | ~Q17,5/mes | ~11 |
| Patrocinio | semana | Q500 mínimo | 1 |
| B2B | cliente | Q1.500+ | 1 |
| CPC por UTM | clic | ~Q0,75 | ~250 |
