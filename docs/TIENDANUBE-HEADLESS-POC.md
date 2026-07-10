# POC: GLÜCK headless sobre Tienda Nube

> Objetivo: mantener **nuestra UI** (diseño, home, fichas de producto, SEO en `gluckbags.com`)
> y sumar **catálogo real + carrito**, delegando el **checkout** a Tienda Nube (TN/Nuvemshop)
> — salvo que sea viable un checkout con UI propia. Este documento arma el plan, con
> posibilidades, dificultades y fases.

---

## 1. Estado actual (punto de partida)

App Flask (no Node) que hoy es un **catálogo-vidriera**, sin e-commerce:

- `app/models/product.py` — `Product`: `title, description, price (int ARS, nullable), currency, category, is_published, position` + `Media` (fotos/videos responsivos). **No hay variantes, ni stock, ni SKU, ni precio obligatorio** (muchos productos muestran "Consultar").
- `app/repositories/product_repository.py` — único punto de acceso a datos (`get_published`, `get_by_id`, `get_published_by_category`…). **Es el "seam" perfecto** para cambiar la fuente de datos.
- `app/routes.py` — home, `/producto/<id>`, `/categoria/<slug>`, páginas estáticas, `sitemap.xml`, JSON-LD SEO.
- **La venta se cierra hoy por Instagram/WhatsApp.** No hay carrito ni pago.
- Persistencia: SQLite (`gluck.db`) + media en disco, panel `/admin` propio.

**Conclusión:** ya tenés la capa de presentación y un repositorio desacoplado. Falta la capa
comercial (catálogo con stock/variantes/precio, carrito, pago, envío, facturación). Eso es
exactamente lo que aporta TN.

---

## 2. Cómo funciona TN headless — hechos que definen la arquitectura

Investigado sobre la doc oficial (`tiendanube.github.io/api-documentation`, DevHub y templates oficiales). Los 4 hechos que mandan:

### 2.1 ⚠️ No hay "Storefront API" pública (a diferencia de Shopify)
La API de TN es **REST admin, OAuth2, server-to-server**. Base:
`https://api.tiendanube.com/2025-03/{store_id}`, con un **token de app instalada en la tienda**.
No es invocable desde el browser sin exponer el token.

> **Implicancia dura:** el navegador **no** puede hablar con TN directo. Nuestro **Flask pasa a
> ser un BFF (backend-for-frontend)**: guarda el token y hace de proxy/caché del catálogo y del
> carrito. Esto encaja bien con lo que ya tenemos (todo pasa por `ProductRepository`).

### 2.2 Catálogo: completo vía API
Products, variantes, categorías, **precio y stock**, imágenes → todo por REST (`read_products`).
Podemos **espejar/cachear** el catálogo de TN en nuestro SQLite y refrescar por **webhooks**
(`product/created|updated|deleted`). TN es la **fuente de verdad**; nosotros renderizamos.

### 2.3 Carrito: existe recurso `Cart`, con matices
- Se crea/manipula server-side (scopes `read_orders`/`write_orders`).
- **Un carrito que ya inició el "Redirect checkout" deja de ser accesible**, y uno convertido a
  Order tampoco. O sea: el carrito es efímero y su ciclo de vida termina en el handoff.
- Existen además recursos `Draft Order` y `Abandoned Checkout`.

### 2.4 Checkout: **no se puede reemplazar la UI nativa** (el punto clave del pedido)
- El modelo soportado y recomendado es **"Redirect checkout"**: armamos el carrito y
  **redirigimos al checkout hosteado de TN**, que resuelve pago (Pago Nube / Mercado Pago),
  **envíos** (Correo Argentino/OCA/Andreani), **impuestos**, **facturación AFIP**, antifraude y
  emails transaccionales.
- Los **formularios del checkout los renderiza siempre el código de TN** con sus estándares. Se
  puede **customizar** (scripts, Checkout SDK, campos, branding) pero **no hostear un checkout
  propio**.
- La única vía para "tomar control total del frontend" del pago es
  `PaymentOptions.Transparent`, pero eso es para **construir una pasarela de pago (payment
  provider app)**, con **certificación rigurosa** — es otra cosa, no aplica a una marca.

> **Respuesta directa a "¿checkout con nuestra UI?":** con TN, **no** de forma soportada. Se
> puede **marquear/estilar** su checkout para que se sienta GLÜCK, pero la página de pago vive en
> `checkout.tiendanube` y la controla TN. Ver §5 para la única alternativa real de UI propia (que
> implica **no** usar el checkout de TN).

---

## 3. Opciones de arquitectura

### ✅ Opción A — Vidriera propia + Redirect checkout (recomendada para el POC)
Nuestra UI para **home, catálogo, ficha y carrito**; al tocar "Finalizar compra" creamos el
carrito en TN y **redirigimos a su checkout**.

- **Ventajas:** conservamos diseño y SEO en `gluckbags.com`; delegamos lo difícil y regulado
  (pagos, envíos, AFIP, antifraude). Es el camino de menor riesgo y menor tiempo.
- **Costo:** perdemos la última milla visual (el pago es de TN, aunque brandeable).
- **Encaje con lo actual:** altísimo — sólo agregamos carrito + un endpoint de handoff.

### 🟡 Opción B — Igual que A, pero con carrito espejado en TN
Creamos el `Cart` en TN apenas el usuario agrega productos (en vez de sólo al final).

- **Ventajas:** totales/promos/stock validados por TN en vivo; habilita "abandoned checkout".
- **Costo:** más llamadas a la API, manejo de expiración/estado del carrito, más superficie de error.
- **Veredicto:** dejarlo para una fase 2; para el POC, carrito local + validación en el handoff alcanza.

### 🔴 Opción C — Checkout 100% propio (UI nuestra de punta a punta)
**No con el checkout de TN.** Implicaría **saltear** TN en el pago e integrar **Mercado Pago
directo** (Checkout API/Bricks, que sí permite UI propia + transparente). Pero entonces perdemos
envíos, facturación AFIP y gestión de orden de TN — habría que construirlos. Es rehacer el
backend comercial y contradice el objetivo "headless sobre TN". Ver §5.

**Recomendación: Opción A**, con puertas abiertas a B.

---

## 4. Diseño técnico del POC (Opción A) sobre el Flask actual

```
Browser (nuestra UI)  ──►  Flask BFF  ──►  Tienda Nube API (OAuth token)
   home/catálogo             - caché catálogo (SQLite)      - products/variants/stock/price
   carrito (localStorage      - /api/cart (server)          - Cart + Redirect checkout
    o sesión)                 - webhooks receiver            - webhooks (product/order)
   "Finalizar" ─────────────► crea Cart en TN ──► 302 a checkout.tiendanube ──► PAGO (TN)
                                                                      │
   /gracias  ◄──────────────── webhook order/paid ◄───────────────────┘
```

**Piezas a construir:**

1. **App/OAuth (una vez):** crear cuenta Partner TN, registrar la app, flujo OAuth para obtener
   y guardar el `access_token` + `store_id`. Nuevo `app/services/tiendanube_client.py` (cliente
   REST con token, reintentos y manejo de rate limit).
2. **Sync de catálogo:** `app/services/catalog_sync.py` que trae products/variants/categorías de
   TN a tablas nuevas (o extiende `Product` con `tn_id, sku, stock, variants`). El
   `ProductRepository` sigue siendo la única fachada → las plantillas casi no cambian.
   - Extender el modelo: agregar `tn_product_id`, `variants` (talle/color), `stock`, y hacer
     `price` confiable (dejar de mostrar "Consultar" en lo vendible).
3. **Webhooks:** endpoint `POST /webhooks/tiendanube` (verificar HMAC) para `product/*` y
   `order/paid|cancelled`, que refresca la caché y dispara el "gracias".
4. **Carrito:** estado en `localStorage`/sesión Flask + endpoints `GET/POST/DELETE /api/cart`.
   Validar precio y stock **contra TN en el momento del handoff** (no confiar en la caché).
5. **Handoff a checkout:** endpoint `POST /checkout` que crea el `Cart` en TN con los line items
   y devuelve `302` a la URL de checkout de TN.
6. **Post-compra:** página `/gracias` + confirmación por webhook `order/paid`. Analítica del
   embudo (add_to_cart, begin_checkout, purchase).
7. **SEO / canónicos:** mantener `gluckbags.com` como único dominio indexable; la tienda TN
   queda como **backoffice + checkout**, `noindex`/oculta, para no duplicar contenido.

---

## 5. La alternativa si "UI propia de checkout" es innegociable

Si el requisito duro es que **toda** la experiencia (incluido el pago) sea nuestra:

- **No usar el checkout de TN.** Usar TN sólo como **catálogo + gestión de órdenes** (crear la
  Order vía API) e integrar **Mercado Pago directo** (Checkout Bricks/API = UI propia y opción
  transparente).
- **Lo que hay que construir nosotros:** cálculo y contratación de envío, **facturación
  electrónica AFIP**, antifraude, emails, conciliación de pagos con la orden.
- **Veredicto:** técnicamente posible, pero es un salto de "POC" a "plataforma": semanas de
  trabajo y responsabilidad regulatoria. **Recomiendo Opción A** y, si el pago propio pesa mucho,
  evaluarlo como fase 3, no como POC.

---

## 6. Dificultades y riesgos (mirados de frente)

| # | Riesgo | Impacto | Mitigación |
|---|--------|---------|------------|
| 1 | **Sin Storefront API** → todo pasa por nuestro backend | Latencia, token a resguardo | BFF con caché; token en secret manager (ya usan Infisical) |
| 2 | **Stock/precio desfasado** entre caché y TN | Overselling, total distinto al mostrado | Webhooks + **revalidación en vivo al hacer checkout** |
| 3 | **Promos/cupones** de TN no reflejadas en nuestra UI | Total del checkout ≠ vidriera → desconfianza | Leer precio efectivo en el handoff; mostrar promos como "se aplican en el pago" |
| 4 | **Corte de experiencia** en el redirect (dominio TN) | Fricción, caída de conversión | Brandear el checkout de TN; medir el embudo; `/gracias` propio |
| 5 | **Modelo de datos:** hoy no hay variantes/stock/SKU | Migración de `Product` | Extender modelo; mapear `tn_id`; TN como fuente de verdad |
| 6 | **Ciclo de vida del Cart** (se vuelve inaccesible tras el redirect) | Bugs de estado | Tratar el carrito local como fuente; el Cart de TN es "de un solo uso" |
| 7 | **Rate limits** de la API | Throttling en sync | Sync incremental por webhooks, no polling; backoff |
| 8 | **Alta como Partner + revisión de app** | Tiempo de arranque | Empezar el registro ya; el POC puede correr en la tienda propia sin publicar la app |
| 9 | **Doble catálogo/SEO** (tienda TN pública + la nuestra) | Contenido duplicado | TN `noindex`/oculta; canónicos a `gluckbags.com` |
| 10 | Webhooks perdidos | Estado inconsistente | Reconciliación periódica (job) además de webhooks |

---

## 7. Plan por fases

**Fase 0 — Habilitación (bloqueante, arrancar ya)**
Cuenta Partner TN · crear app · scopes (`read_products`, `read_orders`, `write_orders`) ·
tienda TN de prueba con 2–3 productos reales. *Entregable: token + store_id funcionando.*

**Fase 1 — Spike de lectura (1–2 días)**
`tiendanube_client.py` + traer products/variants por API e imprimirlos. *Valida auth, forma de
datos y rate limits. Sin tocar la UI.*

**Fase 2 — Catálogo real en nuestra UI (núcleo del POC)**
Extender modelo (variantes/stock/precio) · `catalog_sync` + webhooks `product/*` ·
`ProductRepository` sirve desde la caché sincronizada. *La vidriera ya muestra el catálogo real
de TN, con nuestro diseño.*

**Fase 3 — Carrito + Redirect checkout (cierra el loop de compra)**
Carrito propio (localStorage/sesión) · `/api/cart` · `POST /checkout` que crea el `Cart` en TN
y redirige · página `/gracias` · webhook `order/paid`. *Se puede comprar de punta a punta.*

**Fase 4 — Pulido / decisión**
Brandear checkout TN · analítica del embudo · reconciliación · demo y decisión sobre Opción B
(carrito espejado) o §5 (pago propio con MP).

---

## 8. Decisiones abiertas (necesito tu definición)

1. **¿La tienda TN queda sólo como backoffice/checkout (oculta) o también pública?** (impacta SEO
   y canónicos). *Recomiendo oculta.*
2. **¿El checkout de TN brandeado te sirve, o el pago con UI propia es innegociable?** (define
   Opción A vs. §5). *Recomiendo A para el POC.*
3. **BFF en el mismo Flask** (más simple, un stack) **o servicio Node aparte** usando los
   templates oficiales de TN. *Recomiendo Flask: reusa `ProductRepository` y el deploy actual.*
4. **Alcance del POC:** ¿1 producto de punta a punta (compra real de prueba) o todo el catálogo
   sincronizado? *Recomiendo 1 flujo completo primero.*
