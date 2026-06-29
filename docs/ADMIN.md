# Product Admin

A simple, login-protected panel to manage the products shown on the GLÜCK site:
title, description, price, and multiple photos and videos per product. It is built
for someone with no technical knowledge — uploads happen from the phone, media is
optimized automatically, and changes appear on the site instantly (no deploy).

## For the owner (how to use it)

1. Go to **`https://gluck.nexttech.com.ar/admin`** and enter the password.
2. **Tus productos** lists everything. Use **+ Agregar producto** to add one.
3. In the form:
   - **Título** (required) — the product name.
   - **Precio** (optional) — just the number, e.g. `45000`. Leave empty to show
     “Consultar”.
   - **Categoría** (optional) — pick or type one (Tote, Mini Bag, …).
   - **Descripción** (optional) — details; line breaks are kept.
   - **Fotos y videos** — drag files in, or tap to pick from the phone. You can add
     several photos and videos. **The first one is the cover.** Drag the `⋮⋮` handle
     to reorder; tap `×` to remove one.
   - **Mostrar en la web** — uncheck to keep a product hidden (draft).
4. Tap **Guardar**. A progress bar shows the upload; then it optimizes the media and
   returns to the list. The change is live immediately.
5. On the list you can **Editar**, hide/show (eye icon), or **Borrar** (asks for
   confirmation). Drag `⋮⋮` to change the order products appear on the site.

Photos and videos are compressed and resized automatically, so you can upload
straight from the phone without worrying about file size.

> HEIC note: most phones upload photos as JPEG automatically. If a HEIC photo is
> rejected, set the phone camera to “Most Compatible”, or share/export it as JPEG.

## How it works (for maintainers)

- **Storage:** SQLite DB (`gluck.db`) + a `media/` folder, both under `DATA_DIR`
  (`/data` in Docker, `./instance` locally). In production this is the named Docker
  volume **`gluck-data`**, mounted at `/data` — it survives image rebuilds and
  redeploys. Without that volume, products and uploads would be wiped on every deploy.
- **Media pipeline:** images are resized to responsive WebP+JPEG variants with Pillow;
  videos are transcoded to a compressed web MP4 + poster with **ffmpeg** (installed in
  the Docker image). Files are served from `/media/<...>` with a 1-year immutable cache.
- **Seeding:** on first boot with an empty DB, the 6 originally hardcoded products are
  imported from the shipped static images (so the catalogue isn’t blank). Controlled by
  `SEED_PRODUCTS` (set `0` to disable).
- **Public pages:** the home shop grid is DB-driven; each product has a detail page at
  `/producto/<id>` showing all its photos and videos.

## Configuration (environment)

| Variable         | Purpose                                                            |
|------------------|-------------------------------------------------------------------|
| `ADMIN_PASSWORD` | The single shared password for `/admin`. **Required** in prod.     |
| `SECRET_KEY`     | Flask session secret. If empty, a stable key is generated in `DATA_DIR`. |
| `DATA_DIR`       | Where the DB + media live. `/data` in Docker, `./instance` locally. |
| `SEED_PRODUCTS`  | `1` (default) seeds the initial catalogue on an empty DB; `0` skips. |

In production, set `ADMIN_PASSWORD` (and ideally `SECRET_KEY`) via Infisical so they
are injected at deploy time — never commit them.

## Operations

- **Backups:** back up the `gluck-data` volume regularly (it holds the DB and all
  uploaded media). Example:
  `docker run --rm -v gluck-data:/data -v "$PWD":/backup alpine tar czf /backup/gluck-data-backup.tar.gz -C /data .`
- **Schema changes need a manual migration:** the schema is created with
  `db.create_all()` (no Flask-Migrate). On an existing volume that only creates
  *missing* tables — it never adds a column to an existing one. If you later add a
  field to `Product`/`Media`, you must `ALTER TABLE` on the live `gluck.db` (back it
  up first) or wire up Flask-Migrate, or the first query will fail with
  `no such column`. New tables are created automatically.
- **Big videos / timeouts:** transcoding runs synchronously during the upload request.
  gunicorn is configured with `--timeout 300`. If the site sits behind Cloudflare’s
  free tier, very long uploads can hit its ~100s proxy limit — keep product clips short.
  If long videos become common, move transcoding to a background job.
