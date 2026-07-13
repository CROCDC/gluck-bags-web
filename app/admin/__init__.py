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
import time
import uuid
from collections import defaultdict
from typing import Any

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
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
    new_files = [f for f in request.files.getlist("media") if f and f.filename]

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


def _store_new_file(product: Product, file_storage: Any, errors: list[str]) -> Media | None:
    name = file_storage.filename or "archivo"
    kind = media_service.classify(name)
    if kind is None:
        errors.append(f"«{name}»: formato no soportado.")
        return None

    # Use a random directory name (not the DB id) so the file can be processed
    # BEFORE the row is flushed — no write lock is held during Pillow/ffmpeg.
    slug = uuid.uuid4().hex
    try:
        if kind == "image":
            info = media_service.process_image(file_storage, product.id, slug)
        else:
            info = media_service.process_video(file_storage, product.id, slug)
    except media_service.MediaError as exc:
        errors.append(f"«{name}»: {exc}")
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
