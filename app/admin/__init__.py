"""Admin panel: a dead-simple, login-protected UI to manage products.

Mounted at /admin. One shared password (ADMIN_PASSWORD). Designed to be usable
from a phone by someone non-technical: big buttons, drag-and-drop media, the
first photo is automatically the cover.
"""

from __future__ import annotations

import json
from typing import Any

from flask import (
    Blueprint,
    Flask,
    abort,
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


# --- helpers -----------------------------------------------------------------


def _parse_price(raw: str | None) -> int | None:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return int(digits) if digits else None


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
    existing = {m.id: m for m in product.media}
    new_files = [f for f in request.files.getlist("media") if f and f.filename]

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

    # Delete existing media that the user removed (not referenced in `order`).
    referenced = {
        int(t.split(":", 1)[1]) for t in tokens if t.startswith("existing:") and t.split(":", 1)[1].isdigit()
    }
    for media_id, media in list(existing.items()):
        if media_id not in referenced:
            media_service.delete_media_files(media.path)
            db.session.delete(media)

    ordered: list[Media] = []
    for token in tokens:
        if token.startswith("existing:"):
            key = token.split(":", 1)[1]
            media = existing.get(int(key)) if key.isdigit() else None
            if media is not None and media.id in referenced:
                ordered.append(media)
        elif token.startswith("new:"):
            key = token.split(":", 1)[1]
            if not key.isdigit():
                continue
            idx = int(key)
            if not (0 <= idx < len(new_files)):
                continue
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

    media = Media(product_id=product.id, kind=kind, path="", position=0)
    db.session.add(media)
    db.session.flush()  # assigns media.id for the storage path

    try:
        if kind == "image":
            info = media_service.process_image(file_storage, product.id, media.id)
        else:
            info = media_service.process_video(file_storage, product.id, media.id)
    except media_service.MediaError as exc:
        db.session.delete(media)
        errors.append(f"«{name}»: {exc}")
        return None

    media.kind = info["kind"]
    media.path = info["path"]
    media.width = info["width"]
    media.height = info["height"]
    media.widths = info["widths"]
    return media


# --- routes ------------------------------------------------------------------


@admin_bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if auth.is_logged_in():
        return redirect(url_for("admin.products_list"))
    if request.method == "POST":
        if auth.check_password(request.form.get("password", "")):
            auth.login()
            return redirect(url_for("admin.products_list"))
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

    for message in errors:
        flash(message, "error")
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

    for message in errors:
        flash(message, "error")
    flash("Cambios guardados.", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def product_delete(product_id: int) -> Response:
    product = ProductRepository.get_by_id(product_id)
    if product is None:
        abort(404)
    media_service.delete_product_files(product.id)
    ProductRepository.delete(product)
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
