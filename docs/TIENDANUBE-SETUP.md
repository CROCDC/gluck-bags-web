# Tienda Nube — obtener credenciales (Fase 0)

Guía para conseguir el `TN_STORE_ID` + `TN_ACCESS_TOKEN` que necesita el POC headless
(ver [`TIENDANUBE-HEADLESS-POC.md`](./TIENDANUBE-HEADLESS-POC.md)). Es la única parte
**manual** — requiere tu cuenta de Partner y tu tienda. Se hace **una sola vez**: el
token de Tienda Nube **no vence**.

## 1. Cuenta de Partner
Creá una cuenta gratuita en **partners.tiendanube.com** (Argentina). Es distinta de la
cuenta de tu tienda.

## 2. Crear la app
Panel de Partners (**partners.tiendanube.com**) → **Aplicaciones → Crear aplicación**. Es una
**app**, no una tienda: la app es el conector OAuth que después se instala en tu tienda. Después de
crearla, entrá a la app → pestaña **Datos básicos** (ahí vive la config técnica; ignorá "Datos de
publicación", que es solo para publicar en la tienda de apps pública):
- **Nombre**: p. ej. `GLÜCK Headless POC`.
- **Distribución**: elegí **"Para tus clientes"** (privada) para no requerir homologación/publicación.
- **URL para redirigir después de la instalación (redirect URI)**: `http://localhost:7010/tn/callback`.
  El repo sirve esa ruta (`/tn/callback`) que te muestra el `code` prolijo — corré `python run.py`
  antes de instalar. (El `code` igual aparece en la URL aunque la página no exista.)
- **Permisos (scopes)**: tildá **Products** (→ `read_products`), **Orders** (→ `read/write_orders`)
  y **Manual orders** (→ `read/write_draft_orders`). *El checkout headless se crea como draft order
  (`POST /draft_orders`), así que `write_draft_orders` es imprescindible.*

Al crearla obtenés **App ID (client_id)** y **Client Secret**. Ponelos en `.env`:

```
TN_CLIENT_ID=<app id>
TN_CLIENT_SECRET=<client secret>
```

## 3. Instalar la app en tu tienda
Logueado en tu tienda, abrí en el navegador:

```
https://www.tiendanube.com/apps/<APP_ID>/authorize
```

Autorizás y te redirige a tu redirect URI con `?code=XXXXX` en la barra de direcciones.
**Copiá ese `code`** (dura pocos minutos; si vence, volvé a abrir la URL).

## 4. Canjear el code por el token
```
python scripts/tn_oauth.py <code>
```
Imprime las dos líneas listas para pegar en `.env`:

```
TN_STORE_ID=<user_id>
TN_ACCESS_TOKEN=<access_token>
```

## 5. Validar
```
python scripts/tn_spike.py --all
```
Debería imprimir el nombre de tu tienda, categorías y productos. Si eso anda, el POC
tiene contra qué correr: `/checkout` deja de responder `not_configured` y el resto
de las fases se pueden probar de verdad.

---

### Notas
- **Producción**: no comitees el token. Inyectalo por Infisical (como `ADMIN_PASSWORD`).
- **Tienda de prueba vs. real**: para el POC podés instalar la app en tu tienda real (con
  2–3 productos con precio y stock) sin publicar la app en la tienda de aplicaciones.
- **Rate limits / URLs exactas**: si el panel muestra una URL de autorización o de token
  distinta a la de arriba, usá la del panel (la doc oficial está referenciada pero puede
  variar por región `.com.ar` / `.com.br`). `scripts/tn_oauth.py --url <url>` permite
  overridear el endpoint de token.
