"""Tests for the public site routes (index, product detail, media, robots).

Dimension: public_routes. The public catalogue must show ONLY published
products, render the product detail page (title/price/description/media), serve
uploaded media with long-lived immutable caching, and 404 correctly for
unpublished / missing products and missing media paths.

Products are created the real way — through the admin upload endpoint with an
actual JPEG — so the rendered HTML exercises the same Media URLs / variants the
public templates rely on. Assertions about what the public sees are made as the
anonymous `client` (never logged in).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from flask import Flask
from flask.testing import FlaskClient

# A real JPEG shipped with the app; used as upload payload so media processing
# produces genuine on-disk variants the public templates link to.
_SAMPLE_JPEG = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "static"
    / "img"
    / "productos"
    / "crossbody-rosa.jpg"
)


def _jpeg_bytes() -> bytes:
    return _SAMPLE_JPEG.read_bytes()


def _ffmpeg_available() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


def _make_test_video(dst: Path) -> None:
    """Generate a tiny vertical test clip with ffmpeg for video-cover tests."""
    import subprocess

    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x568:rate=15",
            "-pix_fmt", "yuv420p", str(dst),
        ],
        check=True,
    )


def _create_product(
    auth_client: FlaskClient,
    *,
    title: str,
    price: str | None = None,
    description: str | None = None,
    category: str | None = None,
    is_published: bool = True,
    media: Any = None,
    order: str | None = None,
) -> None:
    """Create a product through the admin form. Defaults to one JPEG cover."""
    data: dict[str, Any] = {"title": title}
    if is_published:
        data["is_published"] = "on"
    if price is not None:
        data["price"] = price
    if description is not None:
        data["description"] = description
    if category is not None:
        data["category"] = category

    if media is None:
        data["media"] = (io.BytesIO(_jpeg_bytes()), "foto.jpg")
        data["order"] = order if order is not None else '["new:0"]'
    else:
        data["media"] = media
        if order is not None:
            data["order"] = order

    resp = auth_client.post(
        "/admin/products/new", data=data, content_type="multipart/form-data"
    )
    # Successful create redirects to the admin list.
    assert resp.status_code == 302, resp.data


def _product_id_by_title(app: Flask, title: str) -> int:
    with app.app_context():
        from app.models import Product

        product = Product.query.filter_by(title=title).first()
        assert product is not None, f"product {title!r} not found"
        return product.id


# --- "/" : index / catalogue --------------------------------------------------


def test_index_returns_200_empty_catalogue(client: FlaskClient) -> None:
    """A blank store still serves the landing page (200)."""
    resp = client.get("/")
    assert resp.status_code == 200


# The inlined stylesheet contains the bare class names ('.shop-grid' /
# '.shop-empty'), so presence checks must target the rendered HTML attribute
# (`class="shop-grid"`), not the substring, to tell the states apart.
_GRID_MARKER = 'class="shop-grid"'
_EMPTY_MARKER = 'class="shop-empty'


def test_index_empty_shows_shop_empty_state(client: FlaskClient) -> None:
    """With no products the shop grid is replaced by the 'shop-empty' state."""
    html = client.get("/").get_data(as_text=True)
    assert _EMPTY_MARKER in html
    # The grid container should not be rendered when there are no products.
    assert _GRID_MARKER not in html


def test_index_lists_published_product(auth_client: FlaskClient, client: FlaskClient) -> None:
    """A published product appears on the home grid with its title."""
    _create_product(auth_client, title="Bolso Publicado", price="45000")

    html = client.get("/").get_data(as_text=True)
    assert _GRID_MARKER in html
    assert _EMPTY_MARKER not in html
    assert "Bolso Publicado" in html


def test_index_shows_formatted_price(auth_client: FlaskClient, client: FlaskClient) -> None:
    """Price renders as es-AR formatted '$ 45.000' on the card."""
    _create_product(auth_client, title="Con Precio", price="45000")

    html = client.get("/").get_data(as_text=True)
    assert "$ 45.000" in html


def test_index_shows_consultar_when_no_price(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    """A product with no price shows the literal 'Consultar'."""
    _create_product(auth_client, title="Sin Precio")  # no price -> None

    html = client.get("/").get_data(as_text=True)
    assert "Sin Precio" in html
    assert "Consultar" in html


def test_index_hides_unpublished_product(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    """An unpublished product never appears in the public grid."""
    _create_product(auth_client, title="Visible Publicado", is_published=True)
    _create_product(auth_client, title="Oculto Borrador", is_published=False)

    html = client.get("/").get_data(as_text=True)
    assert "Visible Publicado" in html
    assert "Oculto Borrador" not in html


def test_index_only_published_after_toggle_off(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """Unpublishing a product removes it from the home grid; the page stays 200."""
    _create_product(auth_client, title="Antes Visible")
    assert "Antes Visible" in client.get("/").get_data(as_text=True)

    pid = _product_id_by_title(app, "Antes Visible")
    auth_client.post(f"/admin/products/{pid}/toggle")

    html = client.get("/").get_data(as_text=True)
    assert "Antes Visible" not in html
    # Now the catalogue is effectively empty for the public.
    assert _EMPTY_MARKER in html


def test_index_video_cover_renders_poster(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    """A product whose only media (cover) is a VIDEO renders on the grid via its
    poster image, without crashing."""
    if not _ffmpeg_available():
        import pytest

        pytest.skip("ffmpeg not available; cannot build a test video")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "clip.mp4"
        _make_test_video(video_path)
        with open(video_path, "rb") as fh:
            video_bytes = fh.read()

    _create_product(
        auth_client,
        title="Producto Con Video",
        media=(io.BytesIO(video_bytes), "clip.mp4"),
        order='["new:0"]',
    )

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Producto Con Video" in html
    # The video cover is shown as a poster <img> on the grid, not a <video>.
    assert "poster.jpg" in html
    # The grid card advertises the video badge.
    assert "Ver video" in html


def test_index_ordered_by_position(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    """Products appear in position order (creation order here = 0,1,2)."""
    _create_product(auth_client, title="Primero")
    _create_product(auth_client, title="Segundo")
    _create_product(auth_client, title="Tercero")

    html = client.get("/").get_data(as_text=True)
    assert html.index("Primero") < html.index("Segundo") < html.index("Tercero")


# --- "/producto/<id>" : product detail ----------------------------------------


def test_detail_200_for_published(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """The detail page of a published product returns 200 and shows its title,
    price, description and media."""
    _create_product(
        auth_client,
        title="Detalle Bolso",
        price="78000",
        description="Hecho a mano en cuero vegano.",
    )
    pid = _product_id_by_title(app, "Detalle Bolso")

    resp = client.get(f"/producto/{pid}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Detalle Bolso" in html
    assert "$ 78.000" in html
    assert "Hecho a mano en cuero vegano." in html
    # Media variants are linked via the /media/ prefix.
    assert "/media/products/" in html


def test_detail_shows_consultar_without_price(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """A priceless product's detail page shows 'Consultar'."""
    _create_product(auth_client, title="Detalle Sin Precio")
    pid = _product_id_by_title(app, "Detalle Sin Precio")

    html = client.get(f"/producto/{pid}").get_data(as_text=True)
    assert "Consultar" in html


def test_detail_404_for_unpublished(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """An unpublished product's detail page 404s for the public."""
    _create_product(auth_client, title="Detalle Oculto", is_published=False)
    pid = _product_id_by_title(app, "Detalle Oculto")

    resp = client.get(f"/producto/{pid}")
    assert resp.status_code == 404


def test_detail_404_for_nonexistent(client: FlaskClient) -> None:
    """A made-up product id 404s."""
    resp = client.get("/producto/999999")
    assert resp.status_code == 404


def test_detail_404_after_unpublish(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """Once a product is unpublished, its (previously 200) detail page 404s."""
    _create_product(auth_client, title="Detalle Toggle")
    pid = _product_id_by_title(app, "Detalle Toggle")
    assert client.get(f"/producto/{pid}").status_code == 200

    auth_client.post(f"/admin/products/{pid}/toggle")
    assert client.get(f"/producto/{pid}").status_code == 404


def test_detail_description_html_is_escaped(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """Raw HTML in the description must be auto-escaped (not executed)."""
    _create_product(
        auth_client,
        title="XSS Bolso",
        description="<script>alert('x')</script>",
    )
    pid = _product_id_by_title(app, "XSS Bolso")

    html = client.get(f"/producto/{pid}").get_data(as_text=True)
    # The raw <script> tag must NOT appear; it should be HTML-escaped instead.
    assert "<script>alert('x')</script>" not in html
    assert "&lt;script&gt;" in html


def test_detail_video_product_renders_video_element(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """A video media renders a <video> with poster + mp4 source on the PDP."""
    if not _ffmpeg_available():
        import pytest

        pytest.skip("ffmpeg not available; cannot build a test video")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "clip.mp4"
        _make_test_video(video_path)
        video_bytes = video_path.read_bytes()

    _create_product(
        auth_client,
        title="PDP Video",
        media=(io.BytesIO(video_bytes), "clip.mp4"),
        order='["new:0"]',
    )
    pid = _product_id_by_title(app, "PDP Video")

    html = client.get(f"/producto/{pid}").get_data(as_text=True)
    assert "<video" in html
    assert "video.mp4" in html
    assert "poster.jpg" in html


# --- "/media/<path>" : uploaded media serving ---------------------------------


def _first_media_rel_path(app: Flask) -> str:
    with app.app_context():
        from app.models import Media

        media = Media.query.first()
        assert media is not None
        return media.path  # e.g. "products/3/7"


def test_media_serves_uploaded_file(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """An uploaded image variant is served over /media/<path> with status 200."""
    _create_product(auth_client, title="Media Bolso")
    rel = _first_media_rel_path(app)

    # The largest jpg variant always exists for a processed image.
    with app.app_context():
        from app.models import Media

        media = Media.query.first()
        assert media is not None
        largest = media.largest_width

    resp = client.get(f"/media/{rel}/{largest}.jpg")
    assert resp.status_code == 200
    assert resp.content_length and resp.content_length > 0


def test_media_cache_control_immutable_long_max_age(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """Served media carries a long-lived, immutable Cache-Control header."""
    _create_product(auth_client, title="Media Cache Bolso")
    rel = _first_media_rel_path(app)
    with app.app_context():
        from app.models import Media

        largest = Media.query.first().largest_width

    resp = client.get(f"/media/{rel}/{largest}.jpg")
    assert resp.status_code == 200

    cache_control = resp.headers.get("Cache-Control", "")
    assert "immutable" in cache_control
    assert "public" in cache_control
    # Long max-age (1 year). Extract the number and assert it is large.
    match = re.search(r"max-age=(\d+)", cache_control)
    assert match is not None, cache_control
    assert int(match.group(1)) >= 31536000


def test_media_404_for_missing_path(client: FlaskClient) -> None:
    """A non-existent media path 404s."""
    resp = client.get("/media/products/9999/9999/1000.jpg")
    assert resp.status_code == 404


def test_media_404_for_missing_in_existing_dir(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """A missing filename inside an existing product dir still 404s."""
    _create_product(auth_client, title="Media Missing File")
    rel = _first_media_rel_path(app)

    resp = client.get(f"/media/{rel}/does-not-exist.jpg")
    assert resp.status_code == 404


# --- "/robots.txt" ------------------------------------------------------------


def test_robots_txt_returns_200(client: FlaskClient) -> None:
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "User-agent" in body


def test_robots_declares_sitemap_on_canonical_domain(client: FlaskClient) -> None:
    """robots.txt advertises the sitemap at the canonical origin (not the alias)."""
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "Sitemap: https://gluckbags.com/sitemap.xml" in body


# --- "/sitemap.xml" -----------------------------------------------------------


def test_sitemap_lists_home_and_published_products(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    """The sitemap is valid XML and lists the home + each published product on the
    canonical domain; unpublished products are excluded."""
    _create_product(auth_client, title="En Sitemap", category="Tote")
    _create_product(auth_client, title="Oculto Sitemap", is_published=False)

    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.mimetype == "application/xml"
    body = resp.get_data(as_text=True)
    assert body.startswith("<?xml")

    locs = re.findall(r"<loc>([^<]+)</loc>", body)
    assert "https://gluckbags.com/" in locs
    assert "https://gluckbags.com/categoria/tote" in locs  # category with products
    # The published product URL is present; the unpublished one is not.
    with_product = [u for u in locs if u.startswith("https://gluckbags.com/producto/")]
    assert len(with_product) == 1
    # Trust pages are listed too.
    assert "https://gluckbags.com/nosotras" in locs


# --- "/categoria/<slug>" ------------------------------------------------------


def test_category_page_lists_its_products(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    """A category page returns 200 and lists only products of that category."""
    _create_product(auth_client, title="Tote Uno", category="Tote")
    _create_product(auth_client, title="Mini Uno", category="Mini Bag")

    html = client.get("/categoria/tote").get_data(as_text=True)
    assert "Tote Uno" in html
    assert "Mini Uno" not in html


def test_category_unknown_slug_404s(client: FlaskClient) -> None:
    resp = client.get("/categoria/no-existe")
    assert resp.status_code == 404


def test_empty_known_category_is_noindex(client: FlaskClient) -> None:
    """A known category card with no products yet renders (200) but is noindex'd."""
    resp = client.get("/categoria/bucket-bag")  # in CATEGORIES, no seeded products
    assert resp.status_code == 200
    assert 'name="robots" content="noindex"' in resp.get_data(as_text=True)


# --- Related products on the PDP ----------------------------------------------


def test_detail_shows_related_products(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """The product detail page cross-links to other products (related strip)."""
    _create_product(auth_client, title="Producto A", category="Tote")
    _create_product(auth_client, title="Producto B", category="Tote")
    pid = _product_id_by_title(app, "Producto A")

    html = client.get(f"/producto/{pid}").get_data(as_text=True)
    assert "pdp-related" in html
    # The sibling product is linked from the related strip.
    assert "Producto B" in html
    assert f"/producto/{_product_id_by_title(app, 'Producto B')}" in html
