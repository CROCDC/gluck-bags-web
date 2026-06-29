"""Regression tests for the hardening applied after the adversarial review."""

from __future__ import annotations

import io
import json

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image

from app.services import media_service

ADMIN_PASSWORD = "test-admin-pw"


def _jpeg_bytes(size: tuple[int, int] = (800, 600), color: tuple[int, int, int] = (180, 120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _create(auth_client: FlaskClient, **data) -> object:
    data.setdefault("title", "P")
    return auth_client.post("/admin/products/new", data=data, content_type="multipart/form-data")


# --- Pillow decompression bomb -----------------------------------------------


def test_decompression_bomb_raises_friendly_error(app: Flask, monkeypatch) -> None:
    """A huge-dimension image is rejected with MediaError, not an OOM / 500."""
    monkeypatch.setattr(media_service.Image, "MAX_IMAGE_PIXELS", 1000)
    big = io.BytesIO()
    Image.new("RGB", (200, 200), (10, 20, 30)).save(big, format="PNG")  # 40k px > 2x cap
    big.seek(0)
    with app.app_context():
        with pytest.raises(media_service.MediaError):
            media_service.process_image(big, product_id=1, media_id="bomb")


# --- Price overflow -----------------------------------------------------------


def test_oversized_price_is_capped_not_500(auth_client: FlaskClient, app: Flask) -> None:
    resp = _create(auth_client, title="Caro", price="9" * 25, order="[]")
    assert resp.status_code == 302  # not a 500
    with app.app_context():
        from app.models import Product

        prod = Product.query.filter_by(title="Caro").first()
        assert prod is not None
        assert prod.price == 9_999_999_999  # clamped under int64


# --- Duplicate order tokens ---------------------------------------------------


def test_duplicate_existing_token_keeps_positions_and_one_cover(
    auth_client: FlaskClient, app: Flask
) -> None:
    img = _jpeg_bytes()
    _create(
        auth_client,
        title="Galería",
        order=json.dumps(["new:0", "new:1"]),
        media=[(io.BytesIO(img), "a.jpg"), (io.BytesIO(img), "b.jpg")],
    )
    with app.app_context():
        from app.models import Product

        prod = Product.query.first()
        pid = prod.id
        ids = [m.id for m in sorted(prod.media, key=lambda m: m.position)]

    # Post a duplicated token for the first media.
    order = json.dumps([f"existing:{ids[0]}", f"existing:{ids[0]}", f"existing:{ids[1]}"])
    resp = auth_client.post(
        f"/admin/products/{pid}/edit",
        data={"title": "Galería", "order": order},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.factory import db
        from app.models import Product

        prod = db.session.get(Product, pid)
        media = sorted(prod.media, key=lambda m: m.position)
        assert [m.position for m in media] == [0, 1]  # contiguous, no gap
        assert sum(1 for m in media if m.is_cover) == 1  # exactly one cover
        assert media[0].is_cover is True


# --- Broken-URL guard ---------------------------------------------------------


def test_image_url_guards_missing_width(app: Flask) -> None:
    from app.models import Media

    with app.app_context():
        m = Media(kind="image", path="products/1/x", widths=None)
        assert m.image_url(None) is None
        assert m.smallest_width is None
        assert m.default_image_url is None
        assert m.image_srcset("jpg") == ""


# --- Transparent PNG ----------------------------------------------------------


def test_transparent_png_flattened_to_white(app: Flask, tmp_path) -> None:
    rgba = Image.new("RGBA", (400, 400), (0, 0, 0, 0))  # fully transparent
    src = tmp_path / "logo.png"
    rgba.save(src, format="PNG")
    with app.app_context():
        info = media_service.process_image(str(src), product_id=2, media_id="png")
        root = app.config["MEDIA_ROOT"]
    import os

    variant = os.path.join(root, info["path"], f"{info['widths'][0]}.jpg")
    pixel = Image.open(variant).convert("RGB").getpixel((5, 5))
    assert min(pixel) > 230, f"transparent area should be white, got {pixel}"


# --- HEIC (iPhone default) ----------------------------------------------------


def test_heic_upload_is_accepted(auth_client: FlaskClient, app: Flask) -> None:
    pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), (120, 90, 70)).save(buf, format="HEIF")
    resp = _create(
        auth_client,
        title="Foto iPhone",
        order=json.dumps(["new:0"]),
        media=(io.BytesIO(buf.getvalue()), "IMG_0001.HEIC"),
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import Product

        prod = Product.query.filter_by(title="Foto iPhone").first()
        assert prod is not None
        assert len(prod.media) == 1
        assert prod.media[0].kind == "image"


# --- Login throttle (production mode) -----------------------------------------


def test_login_is_throttled_after_repeated_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "k")
    from app.factory import create_app

    application = create_app()  # NOT testing -> throttle active
    client = application.test_client()
    headers = {"CF-Connecting-IP": "203.0.113.77"}  # unique IP, no collisions

    statuses = []
    for _ in range(9):
        r = client.post("/admin/login", data={"password": "wrong"}, headers=headers)
        statuses.append(r.status_code)
    assert statuses[-1] == 429, f"expected throttling, got {statuses}"
