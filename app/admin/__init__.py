"""Admin panel: a dead-simple, login-protected UI to manage products.

Mounted at /admin. One shared password (ADMIN_PASSWORD). Designed to be usable
from a phone by someone non-technical: big buttons, drag-and-drop media, the
first photo is automatically the cover.

Scope: this panel edits the LEGACY admin catalogue only. Production serves the
storefront from the Tienda Nube mirror (CATALOG_SOURCE=tiendanube), where the
real catalogue is managed in the TN admin — the UI shows a banner saying so.
This table remains the local-dev source and the rollback fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

from app import auth
from app.auth import login_required
from app.factory import db
from app.models import Media, Product
from app.repositories import ProductRepository
from app.services import media_service

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# Prices are whole pesos; cap well under SQLite's signed-64-bit limit so a pasted
# phone number or fat-fingered value can never overflow the column (which 500s).
_MAX_PRICE = 9_999_999_999

# Simple in-memory login throttle (single-instance app). Keyed by client IP.
_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS = 8
_ATTEMPT_WINDOW_SECONDS = 300


# --- helpers -----------------------------------------------------------------


def _parse_price(raw: str | None) -> int | None:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not digits:
        return None
    return min(int(digits), _MAX_PRICE)


def _client_ip() -> str:
    """Real client IP behind Cloudflare / nginx-proxy (for login throttling)."""
    forwarded = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.remote_addr
    return ip or "?"


def _login_throttled(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < _ATTEMPT_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = recent
    return len(recent) >= _MAX_ATTEMPTS


def _values_from_product(product: Product | None) -> dict[str, Any]:
    if product is None:
        return {"title": "", "description": "", "price": "", "category": "", "is_published": True}
    return {
        "title": product.title,
        "description": product.description or "",
        "price": product.price if product.price is not None else "",
        "category": product.category or "",
        "is_published": product.is_published,
    }


def _values_from_form(form: Any) -> dict[str, Any]:
    return {
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "price": form.get("price", "").strip(),
        "category": form.get("category", "").strip(),
        "is_published": bool(form.get("is_published")),
    }


def _apply_media(product: Product) -> list[str]:
    """Apply the gallery edits posted with the form: order, deletions, new files.

    The form posts a hidden `order` field — a JSON list of tokens like
    "existing:<media_id>" and "new:<index>" in display order — plus the new files
    under `media` (in `new:<index>` order). We rebuild the gallery from that:
    existing media not referenced are deleted, new files are processed, and the
    first item becomes the cover.
    Returns a list of human-friendly error strings for files that failed.
    """
    errors: list[str] = []
    new_files = _pending_uploads()

    # Stage everything WITHOUT flushing: the heavy Pillow/ffmpeg work and the new
    # rows must not open SQLite's write transaction, or its lock would be held for
    # the whole transcode. The single commit in the caller opens it only briefly.
    with db.session.no_autoflush:
        existing = {m.id: m for m in product.media}

        raw_order = (request.form.get("order") or "").strip()
        tokens: list[str] | None = None
        if raw_order:
            try:
                parsed = json.loads(raw_order)
                if isinstance(parsed, list):
                    tokens = [str(t) for t in parsed]
            except ValueError:
                tokens = None
        if tokens is None:
            # No-JS fallback: keep existing order, then append all new files.
            tokens = [f"existing:{m.id}" for m in product.media]
            tokens += [f"new:{i}" for i in range(len(new_files))]

        # Delete existing media the user removed (not referenced in `order`).
        referenced = {
            int(t.split(":", 1)[1])
            for t in tokens
            if t.startswith("existing:") and t.split(":", 1)[1].isdigit()
        }
        for media_id, media in list(existing.items()):
            if media_id not in referenced:
                media_service.delete_media_files(media.path)
                db.session.delete(media)

        # Build the final ordered gallery, de-duping repeated tokens so positions
        # stay contiguous and exactly one item is the cover.
        ordered: list[Media] = []
        seen_existing: set[int] = set()
        seen_new: set[int] = set()
        for token in tokens:
            if token.startswith("existing:"):
                key = token.split(":", 1)[1]
                if not key.isdigit():
                    continue
                media_id = int(key)
                media = existing.get(media_id)
                if media is not None and media_id in referenced and media_id not in seen_existing:
                    seen_existing.add(media_id)
                    ordered.append(media)
            elif token.startswith("new:"):
                key = token.split(":", 1)[1]
                if not key.isdigit():
                    continue
                idx = int(key)
                if idx in seen_new or not (0 <= idx < len(new_files)):
                    continue
                seen_new.add(idx)
                stored = _store_new_file(product, new_files[idx], errors)
                if stored is not None:
                    ordered.append(stored)

        for position, media in enumerate(ordered):
            media.position = position
            media.is_cover = position == 0

    return errors


# Where a browser upload lands before the server processes it into products/<id>/<slug>/.
UPLOAD_STAGING_PREFIX = "uploads"


def _pending_uploads() -> list[Any]:
    """The new gallery items posted with this form, in `new:<index>` order.

    Two shapes, because the 4.5 MB cap on a serverless request body means the file
    cannot always travel through the app: either FileStorages from the multipart form,
    or `{"pathname", "filename"}` entries naming bytes the browser already PUT into the
    object store. The multipart list wins when both are present, so a browser that
    failed to reach the store still saves through the ordinary path.
    """
    files = [f for f in request.files.getlist("media") if f and f.filename]
    if files:
        return files

    try:
        parsed = json.loads(request.form.get("media_uploaded") or "[]")
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []

    uploads: list[Any] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        pathname = str(entry.get("pathname") or "")
        # The pathname decides what the server reads back, so it is confined to the
        # staging area: a crafted value must not be able to name another blob.
        if not pathname.startswith(f"{UPLOAD_STAGING_PREFIX}/") or ".." in pathname:
            continue
        uploads.append({"pathname": pathname, "filename": str(entry.get("filename") or "")})
    return uploads


@contextmanager
def _as_source(item: Any) -> Iterator[Any]:
    """Yield something `media_service` can process, whichever way the bytes arrived.

    A multipart form hands over a FileStorage, which the pipeline already accepts. A
    browser upload that went straight to the object store hands over a pathname instead,
    so the bytes come back down into a temp file — Pillow and ffmpeg both want a real
    path, and the staged original is removed either way once it has been processed.
    """
    if not isinstance(item, dict):
        yield item
        return

    from app.services.media_store import get_store

    store = get_store()
    pathname = item["pathname"]
    suffix = os.path.splitext(pathname)[1] or ".bin"
    handle, temp_path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    try:
        with store.open(pathname) as remote, open(temp_path, "wb") as local:
            shutil.copyfileobj(remote, local)
        yield temp_path
    finally:
        os.unlink(temp_path)
        # The staged original has served its purpose; leaving it behind would bill for
        # a second copy of every photo in the store, forever.
        try:
            store.delete_prefix(pathname)
        except Exception:  # noqa: BLE001 — a stray original must not fail the save
            current_app.logger.warning("No se pudo borrar el original %s", pathname)


def _store_new_file(product: Product, item: Any, errors: list[str]) -> Media | None:
    name = (item.get("filename") if isinstance(item, dict) else item.filename) or "archivo"
    kind = media_service.classify(name)
    if kind is None:
        errors.append(f"«{name}»: formato no soportado.")
        return None

    # Use a random directory name (not the DB id) so the file can be processed
    # BEFORE the row is flushed — no write lock is held during Pillow/ffmpeg.
    slug = uuid.uuid4().hex
    try:
        with _as_source(item) as source:
            if kind == "image":
                info = media_service.process_image(source, product.id, slug)
            else:
                info = media_service.process_video(source, product.id, slug)
    except media_service.MediaError as exc:
        errors.append(f"«{name}»: {exc}")
        return None
    except OSError as exc:
        errors.append(f"«{name}»: no pudimos recuperar el archivo subido ({exc}).")
        return None

    media = Media(
        product_id=product.id,
        kind=info["kind"],
        path=info["path"],
        width=info["width"],
        height=info["height"],
        widths=info["widths"],
        position=0,
    )
    db.session.add(media)
    return media


# --- routes ------------------------------------------------------------------


# What a client token will accept, by kind. Narrow on purpose: the token is handed to a
# browser, and these travel inside its signed payload, so this is the only thing standing
# between a leaked token and arbitrary content in the store.
_UPLOAD_CONTENT_TYPES = {
    "image": [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/heic",
        "image/heif",
        "image/avif",
        "image/tiff",
        "image/bmp",
    ],
    "video": [
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/x-m4v",
        "video/x-matroska",
        "video/3gpp",
        "video/mpeg",
        "video/ogg",
        "video/x-msvideo",
    ],
}


@admin_bp.context_processor
def _inject_upload_mode() -> dict[str, Any]:
    """Whether the form must upload to object storage itself.

    Injected for every admin template rather than passed by each view, so a new view
    cannot forget it and silently fall back to a multipart POST the platform rejects.
    """
    from app.services import media_store

    return {"direct_upload": not media_store.is_local()}


@admin_bp.route("/media/upload-token", methods=["POST"])
@login_required
def media_upload_token() -> Any:
    """Issue a one-file, short-lived token so the browser can upload straight to the store.

    This is what gets a 60 MB clip past the 4.5 MB cap on a function's request body: the
    bytes never pass through the app. Only meaningful for the object-store backend — on a
    filesystem there is nowhere to upload to and the plain multipart form already works.
    """
    from app.services import media_store

    if media_store.is_local():
        abort(404)

    payload = request.get_json(silent=True) or {}
    filename = str(payload.get("filename") or "")
    kind = media_service.classify(filename)
    if kind is None:
        return jsonify({"error": f"«{filename}»: formato no soportado."}), 400

    extension = media_service.ext_of(filename)
    rel_path = f"{UPLOAD_STAGING_PREFIX}/{uuid.uuid4().hex}.{extension}"
    store = media_store.get_store()
    max_bytes = current_app.config["MAX_CONTENT_LENGTH"]
    return jsonify(
        {
            "token": store.client_upload_token(
                rel_path,
                allowed_content_types=_UPLOAD_CONTENT_TYPES[kind],
                maximum_size_in_bytes=max_bytes,
            ),
            "pathname": rel_path,
            "uploadUrl": store.upload_url(rel_path),
            "maxBytes": max_bytes,
        }
    )


@admin_bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if auth.is_logged_in():
        return redirect(url_for("admin.products_list"))
    if request.method == "POST":
        throttle = not current_app.testing
        ip = _client_ip()
        if throttle and _login_throttled(ip):
            flash("Demasiados intentos. Esperá unos minutos y volvé a probar.", "error")
            return render_template("admin/login.html"), 429
        if auth.check_password(request.form.get("password", "")):
            if throttle:
                _LOGIN_ATTEMPTS.pop(ip, None)
            auth.login()
            return redirect(url_for("admin.products_list"))
        if throttle:
            _LOGIN_ATTEMPTS[ip].append(time.time())
        flash("Contraseña incorrecta.", "error")
    return render_template("admin/login.html")


@admin_bp.route("/logout", methods=["POST"])
def logout() -> Response:
    auth.logout()
    return redirect(url_for("admin.login"))


@admin_bp.route("/")
@login_required
def products_list() -> str:
    return render_template("admin/products_list.html", products=ProductRepository.get_all())


@admin_bp.route("/products/new")
@login_required
def product_new() -> str:
    return render_template("admin/product_form.html", product=None, values=_values_from_product(None))


@admin_bp.route("/products/new", methods=["POST"])
@login_required
def product_create() -> Any:
    title = request.form.get("title", "").strip()
    if not title:
        flash("El título es obligatorio.", "error")
        return render_template("admin/product_form.html", product=None, values=_values_from_form(request.form)), 400

    product = ProductRepository.create(
        title=title,
        description=request.form.get("description", "").strip(),
        price=_parse_price(request.form.get("price")),
        category=request.form.get("category", "").strip() or None,
        is_published=bool(request.form.get("is_published")),
    )
    errors = _apply_media(product)
    ProductRepository.save()

    if errors:
        for message in errors:
            flash(message, "error")
        flash("El producto se guardó, pero algunos archivos no se pudieron agregar.", "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))
    flash("Producto guardado.", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/edit")
@login_required
def product_edit(product_id: int) -> str:
    product = ProductRepository.get_by_id(product_id)
    if product is None:
        abort(404)
    return render_template("admin/product_form.html", product=product, values=_values_from_product(product))


@admin_bp.route("/products/<int:product_id>/edit", methods=["POST"])
@login_required
def product_update(product_id: int) -> Any:
    product = ProductRepository.get_by_id(product_id)
    if product is None:
        abort(404)

    title = request.form.get("title", "").strip()
    if not title:
        flash("El título es obligatorio.", "error")
        return (
            render_template("admin/product_form.html", product=product, values=_values_from_form(request.form)),
            400,
        )

    product.title = title
    product.description = request.form.get("description", "").strip()
    product.price = _parse_price(request.form.get("price"))
    product.category = request.form.get("category", "").strip() or None
    product.is_published = bool(request.form.get("is_published"))

    errors = _apply_media(product)
    ProductRepository.save()

    if errors:
        for message in errors:
            flash(message, "error")
        flash("Se guardaron los cambios, pero algunos archivos no se pudieron agregar.", "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))
    flash("Cambios guardados.", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def product_delete(product_id: int) -> Response:
    product = ProductRepository.get_by_id(product_id)
    if product is None:
        abort(404)
    # Commit the DB delete first; only then remove files. If the commit fails the
    # media is still intact (retryable) instead of a live product losing its photos.
    product_id_value = product.id
    ProductRepository.delete(product)
    media_service.delete_product_files(product_id_value)
    flash("Producto eliminado.", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@login_required
def product_toggle(product_id: int) -> Response:
    product = ProductRepository.get_by_id(product_id)
    if product is None:
        abort(404)
    product.is_published = not product.is_published
    ProductRepository.save()
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/reorder", methods=["POST"])
@login_required
def products_reorder() -> Any:
    data = request.get_json(silent=True) or {}
    order = data.get("order", [])
    for position, raw_id in enumerate(order):
        try:
            product = ProductRepository.get_by_id(int(raw_id))
        except (TypeError, ValueError):
            continue
        if product is not None:
            product.position = position
    ProductRepository.save()
    return {"ok": True}


def register_admin(app: Flask) -> None:
    app.register_blueprint(admin_bp)
