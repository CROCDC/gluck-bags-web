"""Edge-case / robustness tests for the admin blueprint (app/admin/__init__.py).

These cover the error and malformed-input branches a non-technical owner can
actually hit but that the happy-path CRUD suite skips: a corrupt photo that has a
valid image EXTENSION but unreadable bytes (so classify() accepts it and the
Pillow decode then fails), tampered/garbled `order` payloads (bad JSON, non-digit
tokens, duplicate / out-of-range new-file indices), the media-failure branch on
EDIT (not just CREATE), and a GET of a non-existent product's edit form.

All run through the `auth_client` (logged-in) test client against a fresh, empty
app, asserting both the HTTP outcome and the resulting DB/media state.
"""

from __future__ import annotations

import io
import json
from typing import Any

from flask import Flask
from flask.testing import FlaskClient

from app.factory import db

_CORRUPT_JPEG = b"this is not a real JPEG, just bytes \x00\x01\x02\x03"


def _good_jpeg() -> bytes:
    """A real, decodable JPEG shipped with the app."""
    import os

    path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "static", "img", "productos", "crossbody-rosa.jpg",
    )
    with open(path, "rb") as fh:
        return fh.read()


def _only_product(app: Flask) -> Any:
    with app.app_context():
        from app.models import Product

        products = Product.query.all()
        assert len(products) == 1, f"expected exactly one product, got {len(products)}"
        p = products[0]
        _ = list(p.media)
        return p


# --- corrupt file with a VALID image extension (process_image raises) ---------


def test_create_with_corrupt_image_extension_saves_product_surfaces_error(
    auth_client: FlaskClient, app: Flask
) -> None:
    """A file named *.jpg whose bytes aren't a real image: classify() accepts the
    extension, then Pillow fails to decode -> MediaError. The product is still
    saved, the friendly decode error is flashed, and no media row is created."""
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": "Foto Rota",
            "order": json.dumps(["new:0"]),
            "media": (io.BytesIO(_CORRUPT_JPEG), "foto.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The MediaError message for an unreadable image (from media_service).
    assert "No pudimos leer la imagen" in body
    assert "algunos archivos no se pudieron agregar" in body

    prod = _only_product(app)
    assert prod.title == "Foto Rota"
    with app.app_context():
        from app.models import Product

        assert list(Product.query.first().media) == []


def test_edit_with_corrupt_image_keeps_changes_surfaces_error(
    auth_client: FlaskClient, app: Flask
) -> None:
    """Editing a product and adding a corrupt photo: the scalar edits are saved,
    the existing media is kept, the bad file is rejected with the edit-specific
    'Se guardaron los cambios…' flash, and we land back on the edit form."""
    # Seed a product with one good image.
    auth_client.post(
        "/admin/products/new",
        data={
            "title": "Original",
            "order": json.dumps(["new:0"]),
            "media": (io.BytesIO(_good_jpeg()), "ok.jpg"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        existing_id = sorted(prod.media, key=lambda m: m.position)[0].id

    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={
            "title": "Editado",
            "order": json.dumps([f"existing:{existing_id}", "new:0"]),
            "media": (io.BytesIO(_CORRUPT_JPEG), "rota.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "No pudimos leer la imagen" in body
    assert "Se guardaron los cambios" in body

    with app.app_context():
        from app.models import Product

        prod = db.session.get(Product, pid)
        # Scalar edit persisted, existing media kept, bad file not added.
        assert prod.title == "Editado"
        media = list(prod.media)
        assert len(media) == 1
        assert media[0].id == existing_id


# --- tampered / malformed `order` payloads ------------------------------------


def test_malformed_order_json_falls_back_to_append_new(
    auth_client: FlaskClient, app: Flask
) -> None:
    """A non-JSON `order` value can't be parsed; the code falls back to keeping
    existing media (none, on create) and appending all new files in order."""
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": "Order Roto",
            "order": "{this is not valid json",
            "media": (io.BytesIO(_good_jpeg()), "foto.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302  # success: the new file was still appended
    with app.app_context():
        from app.models import Product

        media = list(Product.query.first().media)
        assert len(media) == 1
        assert media[0].is_cover is True


def test_order_json_not_a_list_falls_back(auth_client: FlaskClient, app: Flask) -> None:
    """Valid JSON that isn't a list (e.g. an object) is ignored -> append fallback."""
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": "Order Objeto",
            "order": json.dumps({"not": "a list"}),
            "media": (io.BytesIO(_good_jpeg()), "foto.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        assert len(list(Product.query.first().media)) == 1


def test_non_digit_existing_token_is_skipped(auth_client: FlaskClient, app: Flask) -> None:
    """A garbled existing token (existing:abc) is skipped without erroring; the
    valid existing media referenced alongside it survives."""
    auth_client.post(
        "/admin/products/new",
        data={
            "title": "Tokens",
            "order": json.dumps(["new:0"]),
            "media": (io.BytesIO(_good_jpeg()), "foto.jpg"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        real_id = sorted(prod.media, key=lambda m: m.position)[0].id

    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={
            "title": "Tokens",
            "order": json.dumps(["existing:abc", f"existing:{real_id}"]),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Media, Product

        prod = db.session.get(Product, pid)
        media = list(prod.media)
        assert len(media) == 1
        assert media[0].id == real_id
        assert media[0].is_cover is True


def test_duplicate_and_out_of_range_new_tokens_are_deduped(
    auth_client: FlaskClient, app: Flask
) -> None:
    """A duplicated new index and an out-of-range / non-digit new index are all
    skipped: only the single uploaded file is stored, exactly once."""
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": "New Tokens",
            # new:0 valid; new:0 again duplicate; new:5 out of range; new:x non-digit.
            "order": json.dumps(["new:0", "new:0", "new:5", "new:x"]),
            "media": (io.BytesIO(_good_jpeg()), "foto.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        media = list(Product.query.first().media)
        assert len(media) == 1  # one file in, one media out (no dupes/ghosts)
        assert media[0].position == 0
        assert media[0].is_cover is True


# --- GET edit of a non-existent product ---------------------------------------


def test_get_edit_nonexistent_product_404(auth_client: FlaskClient) -> None:
    """Opening the edit form for an id that doesn't exist 404s (not a 500)."""
    resp = auth_client.get("/admin/products/987654/edit")
    assert resp.status_code == 404
