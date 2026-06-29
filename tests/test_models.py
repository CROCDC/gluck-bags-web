"""Unit tests for the Product and Media model helpers.

These exercise the pure-Python logic on the SQLAlchemy models: price
formatting, cover selection, image/video filtering, position ordering and the
Media URL builders (image_url / image_srcset / *_url / thumb_url). Models are
built and persisted inside an app context so the Product<->Media relationship
(and its `order_by="Media.position"`) behaves exactly as in production.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask

from app.factory import db
from app.models import Media, Product
from app.models.media import MEDIA_URL_PREFIX


@pytest.fixture
def ctx(app: Flask) -> Iterator[None]:
    """Provide an app context with a clean transaction per test."""
    with app.app_context():
        yield
        db.session.rollback()


# --- helpers -----------------------------------------------------------------


def _make_product(**kwargs) -> Product:
    """Create + persist a Product with sensible defaults, returning it."""
    defaults: dict = {"title": "Bolso"}
    defaults.update(kwargs)
    product = Product(**defaults)
    db.session.add(product)
    db.session.commit()
    return product


# Sentinel so callers can explicitly request widths=None (DB NULL) instead of
# falling back to the default list.
_DEFAULT_WIDTHS = object()


def _add_image(
    product: Product,
    *,
    position: int = 0,
    is_cover: bool = False,
    widths=_DEFAULT_WIDTHS,
    path: str | None = None,
) -> Media:
    media = Media(
        product_id=product.id,
        kind="image",
        path=path or f"products/{product.id}/{position}",
        widths=[400, 600, 1000] if widths is _DEFAULT_WIDTHS else widths,
        position=position,
        is_cover=is_cover,
    )
    db.session.add(media)
    db.session.commit()
    return media


def _add_video(
    product: Product,
    *,
    position: int = 0,
    is_cover: bool = False,
    path: str | None = None,
) -> Media:
    media = Media(
        product_id=product.id,
        kind="video",
        path=path or f"products/{product.id}/{position}",
        widths=None,
        position=position,
        is_cover=is_cover,
    )
    db.session.add(media)
    db.session.commit()
    return media


# --- Product.formatted_price -------------------------------------------------


class TestFormattedPrice:
    def test_none_price_returns_none(self, ctx: None) -> None:
        product = _make_product(price=None)
        assert product.formatted_price is None

    def test_zero_price_formats(self, ctx: None) -> None:
        product = _make_product(price=0)
        assert product.formatted_price == "$ 0"

    def test_thousand_uses_es_ar_dot(self, ctx: None) -> None:
        product = _make_product(price=1000)
        assert product.formatted_price == "$ 1.000"

    def test_canonical_forty_five_thousand(self, ctx: None) -> None:
        product = _make_product(price=45000)
        assert product.formatted_price == "$ 45.000"

    def test_sub_thousand_has_no_separator(self, ctx: None) -> None:
        product = _make_product(price=999)
        assert product.formatted_price == "$ 999"

    def test_million_groups_twice(self, ctx: None) -> None:
        product = _make_product(price=1234567)
        assert product.formatted_price == "$ 1.234.567"

    def test_exact_million(self, ctx: None) -> None:
        product = _make_product(price=1000000)
        assert product.formatted_price == "$ 1.000.000"

    def test_separator_is_dot_not_comma(self, ctx: None) -> None:
        """es-AR groups thousands with dots; there must be no commas at all."""
        product = _make_product(price=2500000)
        assert "," not in product.formatted_price
        assert product.formatted_price == "$ 2.500.000"


# --- Product.cover -----------------------------------------------------------


class TestCover:
    def test_none_when_no_media(self, ctx: None) -> None:
        product = _make_product()
        assert product.cover is None

    def test_first_by_position_when_no_flag(self, ctx: None) -> None:
        product = _make_product()
        # Insert out of position order; the relationship orders by position.
        _add_image(product, position=2)
        first = _add_image(product, position=0)
        _add_image(product, position=1)
        assert product.cover is first

    def test_flagged_cover_wins_over_first(self, ctx: None) -> None:
        product = _make_product()
        _add_image(product, position=0)
        flagged = _add_image(product, position=1, is_cover=True)
        _add_image(product, position=2)
        assert product.cover is flagged

    def test_flagged_cover_wins_even_if_last(self, ctx: None) -> None:
        product = _make_product()
        _add_image(product, position=0)
        _add_image(product, position=1)
        flagged = _add_image(product, position=5, is_cover=True)
        assert product.cover is flagged

    def test_first_flagged_cover_wins_if_multiple(self, ctx: None) -> None:
        """If (incorrectly) several are flagged, the lowest-position one wins."""
        product = _make_product()
        low = _add_image(product, position=0, is_cover=True)
        _add_image(product, position=1, is_cover=True)
        assert product.cover is low

    def test_cover_can_be_a_video(self, ctx: None) -> None:
        product = _make_product()
        _add_image(product, position=1)
        video_cover = _add_video(product, position=0, is_cover=True)
        assert product.cover is video_cover


# --- Product.images / Product.videos / ordering ------------------------------


class TestImagesVideosOrdering:
    def test_images_only(self, ctx: None) -> None:
        product = _make_product()
        img_a = _add_image(product, position=0)
        _add_video(product, position=1)
        img_b = _add_image(product, position=2)
        assert product.images == [img_a, img_b]
        assert all(m.is_image for m in product.images)

    def test_videos_only(self, ctx: None) -> None:
        product = _make_product()
        _add_image(product, position=0)
        vid = _add_video(product, position=1)
        assert product.videos == [vid]
        assert all(m.is_video for m in product.videos)

    def test_empty_filters(self, ctx: None) -> None:
        product = _make_product()
        assert product.images == []
        assert product.videos == []

    def test_media_ordered_by_position(self, ctx: None) -> None:
        product = _make_product()
        _add_image(product, position=3)
        _add_image(product, position=1)
        _add_video(product, position=2)
        _add_image(product, position=0)
        positions = [m.position for m in product.media]
        assert positions == [0, 1, 2, 3]

    def test_images_preserve_position_order(self, ctx: None) -> None:
        product = _make_product()
        # Add images at non-sequential positions, out of insertion order.
        a = _add_image(product, position=4)
        b = _add_image(product, position=1)
        c = _add_image(product, position=7)
        assert product.images == [b, a, c]

    def test_videos_preserve_position_order(self, ctx: None) -> None:
        product = _make_product()
        a = _add_video(product, position=5)
        b = _add_video(product, position=2)
        assert product.videos == [b, a]

    def test_cascade_delete_removes_media(self, ctx: None) -> None:
        product = _make_product()
        _add_image(product, position=0)
        _add_video(product, position=1)
        pid = product.id
        db.session.delete(product)
        db.session.commit()
        assert Media.query.filter_by(product_id=pid).count() == 0


# --- Media.is_image / is_video -----------------------------------------------


class TestMediaKind:
    def test_image_flags(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product)
        assert media.is_image is True
        assert media.is_video is False

    def test_video_flags(self, ctx: None) -> None:
        product = _make_product()
        media = _add_video(product)
        assert media.is_video is True
        assert media.is_image is False


# --- Media image URL helpers -------------------------------------------------


class TestMediaImageUrls:
    def test_image_url_default_ext_jpg(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/1/7", widths=[400, 600, 1000])
        assert media.image_url(600) == "/media/products/1/7/600.jpg"

    def test_image_url_explicit_webp(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/1/7")
        assert media.image_url(400, ext="webp") == "/media/products/1/7/400.webp"

    def test_image_url_starts_with_media_prefix_and_path(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/9/3")
        url = media.image_url(1000)
        assert url.startswith(f"{MEDIA_URL_PREFIX}/{media.path}/")
        assert url.startswith("/media/products/9/3/")

    def test_srcset_jpg(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/2/5", widths=[400, 600, 1000])
        expected = (
            "/media/products/2/5/400.jpg 400w, "
            "/media/products/2/5/600.jpg 600w, "
            "/media/products/2/5/1000.jpg 1000w"
        )
        assert media.image_srcset() == expected

    def test_srcset_webp(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/2/5", widths=[400, 600, 1000])
        expected = (
            "/media/products/2/5/400.webp 400w, "
            "/media/products/2/5/600.webp 600w, "
            "/media/products/2/5/1000.webp 1000w"
        )
        assert media.image_srcset(ext="webp") == expected

    def test_srcset_preserves_widths_order(self, ctx: None) -> None:
        """Srcset entries follow the order stored in `widths`, not sorted."""
        product = _make_product()
        media = _add_image(product, path="products/3/3", widths=[1000, 400, 600])
        assert media.image_srcset() == (
            "/media/products/3/3/1000.jpg 1000w, "
            "/media/products/3/3/400.jpg 400w, "
            "/media/products/3/3/600.jpg 600w"
        )

    def test_srcset_empty_when_no_widths(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/4/1", widths=[])
        assert media.image_srcset() == ""
        assert media.image_srcset(ext="webp") == ""

    def test_srcset_handles_none_widths(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/4/2", widths=None)
        assert media.image_srcset() == ""

    def test_largest_and_smallest_width(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, widths=[600, 400, 1000])
        assert media.largest_width == 1000
        assert media.smallest_width == 400

    def test_largest_smallest_single_width(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, widths=[800])
        assert media.largest_width == 800
        assert media.smallest_width == 800

    def test_largest_smallest_none_when_empty(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, widths=[])
        assert media.largest_width is None
        assert media.smallest_width is None

    def test_largest_smallest_none_when_widths_none(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, widths=None)
        assert media.largest_width is None
        assert media.smallest_width is None

    def test_default_image_url_is_largest_jpg(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/5/2", widths=[400, 600, 1000])
        assert media.default_image_url == "/media/products/5/2/1000.jpg"

    def test_default_image_url_none_without_widths(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, widths=[])
        assert media.default_image_url is None


# --- Media video URL helpers -------------------------------------------------


class TestMediaVideoUrls:
    def test_video_url(self, ctx: None) -> None:
        product = _make_product()
        media = _add_video(product, path="products/6/4")
        assert media.video_url == "/media/products/6/4/video.mp4"

    def test_poster_url(self, ctx: None) -> None:
        product = _make_product()
        media = _add_video(product, path="products/6/4")
        assert media.poster_url == "/media/products/6/4/poster.jpg"

    def test_video_urls_start_with_media_prefix_and_path(self, ctx: None) -> None:
        product = _make_product()
        media = _add_video(product, path="products/8/8")
        assert media.video_url.startswith(f"/media/{media.path}/")
        assert media.poster_url.startswith(f"/media/{media.path}/")


# --- Media.thumb_url ---------------------------------------------------------


class TestThumbUrl:
    def test_image_thumb_is_smallest_webp(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/7/1", widths=[400, 600, 1000])
        assert media.thumb_url == "/media/products/7/1/400.webp"

    def test_image_thumb_falls_back_to_default_jpg_without_widths(self, ctx: None) -> None:
        """No widths -> no smallest -> falls back to default_image_url (None here)."""
        product = _make_product()
        media = _add_image(product, widths=[])
        # smallest_width is None, so thumb falls back to default_image_url, also None.
        assert media.thumb_url is None

    def test_video_thumb_is_poster(self, ctx: None) -> None:
        product = _make_product()
        media = _add_video(product, path="products/7/9")
        assert media.thumb_url == "/media/products/7/9/poster.jpg"

    def test_thumb_url_starts_with_media_prefix(self, ctx: None) -> None:
        product = _make_product()
        img = _add_image(product, path="products/7/2", widths=[400, 600])
        vid = _add_video(product, path="products/7/3")
        assert img.thumb_url.startswith("/media/products/7/2/")
        assert vid.thumb_url.startswith("/media/products/7/3/")


# --- Cross-cutting URL prefix invariants -------------------------------------


class TestUrlPrefixInvariant:
    def test_all_image_urls_start_with_media_path(self, ctx: None) -> None:
        product = _make_product()
        media = _add_image(product, path="products/10/1", widths=[400, 600, 1000])
        base = f"/media/{media.path}/"
        urls = [
            media.image_url(400),
            media.image_url(600, ext="webp"),
            media.default_image_url,
            media.thumb_url,
        ]
        for url in urls:
            assert url is not None
            assert url.startswith(base), url
        # Each srcset entry's URL must also live under the same path.
        for entry in media.image_srcset().split(", "):
            assert entry.startswith(base), entry

    def test_media_url_prefix_constant(self) -> None:
        assert MEDIA_URL_PREFIX == "/media"
