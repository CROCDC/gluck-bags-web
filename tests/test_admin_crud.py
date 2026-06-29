"""Integration tests for the admin product lifecycle (CRUD via the test client).

These exercise the admin blueprint end to end through multipart POSTs with the
`auth_client` fixture (already logged in): create / edit / delete / toggle /
reorder, including price parsing, the `is_published` checkbox, gallery ordering
(cover selection), media deletion (DB row + files on disk), unsupported files,
and cascade cleanup. Disk effects are verified by reading MEDIA_ROOT inside an
app context.

All tests are hermetic: each `auth_client`/`app` pair is a fresh, empty app with
its own temp DATA_DIR (SEED_PRODUCTS=0), so nothing leaks between tests.
"""

from __future__ import annotations

import io
import json
import os
from typing import Any

from flask import Flask
from flask.testing import FlaskClient

from app.factory import db

# A real, decodable JPEG shipped with the app — Pillow can open it, so
# process_image() produces the on-disk variants we assert about.
SAMPLE_JPEG = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "app",
    "static",
    "img",
    "productos",
    "crossbody-rosa.jpg",
)


# --- helpers -----------------------------------------------------------------


def _jpeg_bytes() -> bytes:
    with open(SAMPLE_JPEG, "rb") as fh:
        return fh.read()


def _media_root(app: Flask) -> str:
    return app.config["MEDIA_ROOT"]


def _product_dir(app: Flask, product_id: int) -> str:
    return os.path.join(_media_root(app), "products", str(product_id))


def _media_dir(app: Flask, media: Any) -> str:
    # The media directory is a random slug stored in media.path, not the DB id.
    return os.path.join(_media_root(app), media.path)


def _create_with_images(
    auth_client: FlaskClient,
    n: int,
    *,
    title: str = "Bolso",
    **extra: Any,
) -> Any:
    """POST /admin/products/new with `n` JPEG images in explicit order."""
    img = _jpeg_bytes()
    files = [(io.BytesIO(img), f"foto{i}.jpg") for i in range(n)]
    order = json.dumps([f"new:{i}" for i in range(n)])
    data: dict[str, Any] = {"title": title, "is_published": "on", "order": order, "media": files}
    data.update(extra)
    return auth_client.post("/admin/products/new", data=data, content_type="multipart/form-data")


def _only_product(app: Flask) -> Any:
    """Return the single product in the DB (fails the test if not exactly one)."""
    with app.app_context():
        from app.models import Product

        products = Product.query.all()
        assert len(products) == 1, f"expected exactly one product, got {len(products)}"
        # Eager-load media inside the context so callers can read it after.
        p = products[0]
        _ = list(p.media)
        return p


# --- create: validation ------------------------------------------------------


def test_create_requires_title_returns_400(auth_client: FlaskClient, app: Flask) -> None:
    resp = auth_client.post(
        "/admin/products/new",
        data={"title": "   ", "description": "algo"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    with app.app_context():
        from app.models import Product

        assert Product.query.count() == 0


def test_create_missing_title_rerenders_keeping_values(auth_client: FlaskClient) -> None:
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": "",
            "description": "Una descripcion entrada",
            "price": "12.345",
            "category": "Tote",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    # The form re-renders with the values the user typed so nothing is lost.
    assert "Una descripcion entrada" in body
    assert "12.345" in body
    assert "Tote" in body


# --- create: price parsing ---------------------------------------------------


def test_price_plain_digits(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "price": "45000"},
        content_type="multipart/form-data",
    )
    assert _only_product(app).price == 45000


def test_price_with_dot_thousands(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "price": "45.000"},
        content_type="multipart/form-data",
    )
    assert _only_product(app).price == 45000


def test_price_with_currency_symbol_and_spaces(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "price": "$ 45.000"},
        content_type="multipart/form-data",
    )
    assert _only_product(app).price == 45000


def test_price_empty_is_none(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "price": ""},
        content_type="multipart/form-data",
    )
    assert _only_product(app).price is None


def test_price_non_numeric_is_none(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "price": "consultar"},
        content_type="multipart/form-data",
    )
    assert _only_product(app).price is None


# --- create: category & is_published -----------------------------------------


def test_category_empty_is_none(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "category": "   "},
        content_type="multipart/form-data",
    )
    assert _only_product(app).category is None


def test_category_value_kept(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "category": "Mini Bag"},
        content_type="multipart/form-data",
    )
    assert _only_product(app).category == "Mini Bag"


def test_is_published_present_is_true(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P", "is_published": "on"},
        content_type="multipart/form-data",
    )
    assert _only_product(app).is_published is True


def test_is_published_absent_is_false(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "P"},
        content_type="multipart/form-data",
    )
    assert _only_product(app).is_published is False


# --- create: with media ------------------------------------------------------


def test_create_with_no_media(auth_client: FlaskClient, app: Flask) -> None:
    resp = auth_client.post(
        "/admin/products/new",
        data={"title": "Sin fotos"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302  # redirect to the list on success
    p = _only_product(app)
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        assert list(prod.media) == []
        assert prod.cover is None


def test_create_with_one_image(auth_client: FlaskClient, app: Flask) -> None:
    resp = _create_with_images(auth_client, 1, title="Una foto")
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        media = list(prod.media)
        assert len(media) == 1
        m = media[0]
        assert m.kind == "image"
        assert m.is_cover is True
        assert m.position == 0
        assert prod.cover is not None and prod.cover.id == m.id
        # The responsive variants were actually written to disk.
        d = _media_dir(app, m)
        assert os.path.isdir(d)
        files = set(os.listdir(d))
        assert any(f.endswith(".jpg") for f in files)
        assert any(f.endswith(".webp") for f in files)


def test_create_with_multiple_images_first_is_cover(auth_client: FlaskClient, app: Flask) -> None:
    resp = _create_with_images(auth_client, 3, title="Tres fotos")
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        media = sorted(prod.media, key=lambda m: m.position)
        assert len(media) == 3
        # Positions are a clean 0..n-1 sequence.
        assert [m.position for m in media] == [0, 1, 2]
        # Only the first item is the cover.
        assert media[0].is_cover is True
        assert all(not m.is_cover for m in media[1:])
        assert prod.cover.id == media[0].id


# --- edit: scalar fields -----------------------------------------------------


def test_edit_changes_scalar_fields(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "Original", "price": "1000", "category": "Tote", "is_published": "on"},
        content_type="multipart/form-data",
    )
    pid = _only_product(app).id

    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={
            "title": "Editado",
            "description": "Nueva desc",
            "price": "2.500",
            "category": "Clutch",
            # is_published omitted -> should flip to False
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        p = db.session.get(Product, pid)
        assert p.title == "Editado"
        assert p.description == "Nueva desc"
        assert p.price == 2500
        assert p.category == "Clutch"
        assert p.is_published is False


def test_edit_requires_title_400_keeps_product(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "Tiene titulo"},
        content_type="multipart/form-data",
    )
    pid = _only_product(app).id
    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={"title": "", "description": "x"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    with app.app_context():
        from app.models import Product

        # The product is untouched, title preserved.
        assert db.session.get(Product, pid).title == "Tiene titulo"


def test_edit_nonexistent_product_404(auth_client: FlaskClient) -> None:
    resp = auth_client.post(
        "/admin/products/99999/edit",
        data={"title": "X"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 404


# --- edit: gallery / media ordering ------------------------------------------


def test_edit_reorder_swaps_the_cover(auth_client: FlaskClient, app: Flask) -> None:
    _create_with_images(auth_client, 2, title="Dos fotos")
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        media = sorted(prod.media, key=lambda m: m.position)
        first_id, second_id = media[0].id, media[1].id
        assert media[0].is_cover and not media[1].is_cover

    # Re-submit with the order reversed; the new first item must become cover.
    order = json.dumps([f"existing:{second_id}", f"existing:{first_id}"])
    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={"title": "Dos fotos", "order": order},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Media

        new_first = db.session.get(Media, second_id)
        new_second = db.session.get(Media, first_id)
        assert new_first.position == 0 and new_first.is_cover is True
        assert new_second.position == 1 and new_second.is_cover is False


def test_edit_remove_media_deletes_row_and_files(auth_client: FlaskClient, app: Flask) -> None:
    _create_with_images(auth_client, 2, title="Dos fotos")
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        media = sorted(prod.media, key=lambda m: m.position)
        keep_id, drop_id = media[0].id, media[1].id
        drop_dir = _media_dir(app, media[1])
        keep_dir = _media_dir(app, media[0])
    assert os.path.isdir(drop_dir)

    # Submit only the kept token; the dropped media should vanish from DB + disk.
    order = json.dumps([f"existing:{keep_id}"])
    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={"title": "Dos fotos", "order": order},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Media

        assert db.session.get(Media, drop_id) is None
        assert db.session.get(Media, keep_id) is not None
    assert not os.path.exists(drop_dir), "dropped media dir should be deleted from disk"
    assert os.path.isdir(keep_dir), "kept media dir should still exist"


def test_edit_add_new_media_keeping_existing(auth_client: FlaskClient, app: Flask) -> None:
    _create_with_images(auth_client, 1, title="Una foto")
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        existing_id = sorted(prod.media, key=lambda m: m.position)[0].id

    img = _jpeg_bytes()
    order = json.dumps([f"existing:{existing_id}", "new:0"])
    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={
            "title": "Una foto",
            "order": order,
            "media": (io.BytesIO(img), "nueva.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        prod = db.session.get(Product, pid)
        media = sorted(prod.media, key=lambda m: m.position)
        assert len(media) == 2
        assert media[0].id == existing_id
        assert media[0].is_cover is True and media[0].position == 0
        # The newly added media is a distinct row at position 1, not cover.
        assert media[1].id != existing_id
        assert media[1].position == 1 and media[1].is_cover is False
        assert os.path.isdir(_media_dir(app, media[1]))


def test_edit_no_order_field_keeps_existing_then_appends_new(
    auth_client: FlaskClient, app: Flask
) -> None:
    """No-JS fallback: existing media kept in order, then the new file appended."""
    _create_with_images(auth_client, 1, title="Una foto")
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        existing_id = sorted(prod.media, key=lambda m: m.position)[0].id

    img = _jpeg_bytes()
    # Note: NO `order` field at all -> fallback path in _apply_media.
    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={"title": "Una foto", "media": (io.BytesIO(img), "extra.jpg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        prod = db.session.get(Product, pid)
        media = sorted(prod.media, key=lambda m: m.position)
        assert len(media) == 2
        # Existing one stays first (and cover), new one appended after.
        assert media[0].id == existing_id
        assert media[0].position == 0 and media[0].is_cover is True
        assert media[1].id != existing_id
        assert media[1].position == 1 and media[1].is_cover is False


# --- create/edit: unsupported file ------------------------------------------


def test_unsupported_file_skipped_product_still_saved(
    auth_client: FlaskClient, app: Flask
) -> None:
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": "Con archivo malo",
            "order": json.dumps(["new:0"]),
            "media": (io.BytesIO(b"not an image"), "notas.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    # The product is saved (we land on the list) and an error is surfaced.
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "no soportado" in body.lower()
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        assert prod is not None
        assert prod.title == "Con archivo malo"
        # The bad file produced no media.
        assert list(prod.media) == []


def test_mixed_good_and_bad_files_keeps_good_skips_bad(
    auth_client: FlaskClient, app: Flask
) -> None:
    img = _jpeg_bytes()
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": "Mixto",
            "order": json.dumps(["new:0", "new:1"]),
            "media": [
                (io.BytesIO(img), "buena.jpg"),
                (io.BytesIO(b"junk"), "mala.txt"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        media = list(prod.media)
        # Only the valid image survived; it is the cover at position 0.
        assert len(media) == 1
        assert media[0].kind == "image"
        assert media[0].is_cover is True
        assert media[0].position == 0


# --- delete ------------------------------------------------------------------


def test_delete_removes_row_cascade_media_and_disk_dir(
    auth_client: FlaskClient, app: Flask
) -> None:
    _create_with_images(auth_client, 2, title="A borrar")
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        media_ids = [m.id for m in prod.media]
    pdir = _product_dir(app, pid)
    assert os.path.isdir(pdir)

    resp = auth_client.post(f"/admin/products/{pid}/delete")
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Media, Product

        assert db.session.get(Product, pid) is None
        # Media rows are cascade-deleted along with the product.
        for mid in media_ids:
            assert db.session.get(Media, mid) is None
    assert not os.path.exists(pdir), "product media dir should be removed from disk"


def test_delete_nonexistent_404(auth_client: FlaskClient) -> None:
    resp = auth_client.post("/admin/products/424242/delete")
    assert resp.status_code == 404


# --- toggle ------------------------------------------------------------------


def test_toggle_flips_is_published(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "Toggle", "is_published": "on"},
        content_type="multipart/form-data",
    )
    pid = _only_product(app).id
    with app.app_context():
        from app.models import Product

        assert db.session.get(Product, pid).is_published is True

    resp = auth_client.post(f"/admin/products/{pid}/toggle")
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        assert db.session.get(Product, pid).is_published is False

    auth_client.post(f"/admin/products/{pid}/toggle")
    with app.app_context():
        from app.models import Product

        assert db.session.get(Product, pid).is_published is True


def test_toggle_nonexistent_404(auth_client: FlaskClient) -> None:
    resp = auth_client.post("/admin/products/777777/toggle")
    assert resp.status_code == 404


# --- reorder endpoint --------------------------------------------------------


def test_reorder_updates_positions_and_returns_ok(auth_client: FlaskClient, app: Flask) -> None:
    ids: list[int] = []
    for i in range(3):
        auth_client.post(
            "/admin/products/new",
            data={"title": f"P{i}"},
            content_type="multipart/form-data",
        )
    with app.app_context():
        from app.models import Product

        ids = [p.id for p in Product.query.order_by(Product.id.asc()).all()]
    assert len(ids) == 3

    # Reverse the order; positions must follow the posted list.
    new_order = list(reversed(ids))
    resp = auth_client.post("/admin/products/reorder", json={"order": new_order})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    with app.app_context():
        from app.models import Product

        for expected_pos, pid in enumerate(new_order):
            assert db.session.get(Product, pid).position == expected_pos


def test_reorder_ignores_unknown_ids(auth_client: FlaskClient, app: Flask) -> None:
    auth_client.post(
        "/admin/products/new",
        data={"title": "Solo"},
        content_type="multipart/form-data",
    )
    pid = _only_product(app).id
    # A bad id and a non-numeric token are skipped without erroring.
    resp = auth_client.post(
        "/admin/products/reorder",
        json={"order": [999999, "abc", pid]},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    with app.app_context():
        from app.models import Product

        # `pid` is at index 2 in the posted order.
        assert db.session.get(Product, pid).position == 2


def test_reorder_empty_order_is_ok(auth_client: FlaskClient) -> None:
    resp = auth_client.post("/admin/products/reorder", json={"order": []})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


# --- auth gating (sanity) ----------------------------------------------------


def test_admin_routes_require_login(client: FlaskClient) -> None:
    """An anonymous client is redirected to login, not allowed to mutate."""
    resp = client.get("/admin/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]
