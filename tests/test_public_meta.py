"""Tests for public <head> metadata: Open Graph / Twitter cards, favicon /
font preloads, document language, and conditional Umami analytics.

Dimension: public_meta. These tags drive link-preview rendering (WhatsApp /
Facebook / Twitter), first-paint font behaviour, and privacy-friendly analytics
in production. The home page emits the default "website" social card; the product
detail page OVERRIDES it with an og:type=product card and a product-specific
og:image taken from the cover. Umami must inject ONLY when configured.

Everything is asserted against the rendered HTML (the same bytes a crawler /
browser would receive), created the real way through the admin upload endpoint so
the cover image URL exercised by og:image matches the on-disk media variants.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient

_BRAND = "GLÜCK"


def _has_meta(html: str, *, key: str, attr: str, content: str) -> bool:
    """True if a <meta {attr}="{key}" content="{content}"> tag is present.

    The templates align attributes with runs of whitespace, so match the tag
    structure with a whitespace-tolerant regex instead of an exact string."""
    pattern = (
        r'<meta\s+'
        + re.escape(attr) + r'="' + re.escape(key) + r'"\s+'
        + r'content="' + re.escape(content) + r'"'
    )
    return re.search(pattern, html) is not None

# A real JPEG shipped with the app; uploading it produces genuine media variants
# so the product og:image points at an actually-generated /media/.../<w>.jpg.
_SAMPLE_JPEG = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "static"
    / "img"
    / "productos"
    / "crossbody-rosa.jpg"
)


def _create_published_product_with_image(
    auth_client: FlaskClient, *, title: str
) -> None:
    """Create a published product with one JPEG cover via the admin form."""
    resp = auth_client.post(
        "/admin/products/new",
        data={
            "title": title,
            "is_published": "on",
            "media": (io.BytesIO(_SAMPLE_JPEG.read_bytes()), "foto.jpg"),
            "order": '["new:0"]',
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302, resp.data


def _product_id_by_title(app: Flask, title: str) -> int:
    with app.app_context():
        from app.models import Product

        product = Product.query.filter_by(title=title).first()
        assert product is not None, f"product {title!r} not found"
        return product.id


# --- 1) HOME Open Graph / Twitter card ----------------------------------------


def test_home_open_graph_and_twitter_card(client: FlaskClient) -> None:
    """The home page emits the default 'website' social card with the brand
    title, the absolute og-image.jpg (1200x630), site_name, locale and a
    summary_large_image Twitter card."""
    html = client.get("/").get_data(as_text=True)

    assert _has_meta(html, key="og:type", attr="property", content="website")
    # og:title carries the brand (rendered from the {{ brand }} context global).
    assert _has_meta(
        html,
        key="og:title",
        attr="property",
        content=f"{_BRAND} · Bolsos minimalistas de cuero vegano",
    )
    # Absolute og:image so crawlers can fetch it without resolving relatives.
    assert _has_meta(
        html,
        key="og:image",
        attr="property",
        content="https://gluckbags.com/static/img/og-image.jpg",
    )
    assert _has_meta(html, key="og:image:width", attr="property", content="1200")
    assert _has_meta(html, key="og:image:height", attr="property", content="630")
    assert _has_meta(html, key="og:site_name", attr="property", content=_BRAND)
    assert _has_meta(html, key="og:locale", attr="property", content="es_AR")
    assert _has_meta(
        html, key="twitter:card", attr="name", content="summary_large_image"
    )


def _canonical_of(html: str) -> str | None:
    match = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    return match.group(1) if match else None


def _jsonld_types(html: str) -> list[str]:
    """All @type values across every JSON-LD <script> on the page."""
    import json

    types: list[str] = []
    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    ):
        data = json.loads(block)
        for obj in data if isinstance(data, list) else [data]:
            if obj.get("@type"):
                types.append(obj["@type"])
    return types


# --- 1b) Canonical + JSON-LD structured data ----------------------------------


def test_home_canonical_and_structured_data(client: FlaskClient) -> None:
    """The home self-canonicalizes to the apex and ships Organization + WebSite."""
    html = client.get("/").get_data(as_text=True)
    assert _canonical_of(html) == "https://gluckbags.com/"
    assert set(_jsonld_types(html)) >= {"Organization", "WebSite"}


def test_query_params_collapse_to_clean_canonical(client: FlaskClient) -> None:
    """Tracking/search params don't fork the canonical: ?utm=, ?s= -> apex root."""
    for path in ("/?utm_source=newsletter", "/?s=cualquier-cosa"):
        html = client.get(path).get_data(as_text=True)
        assert _canonical_of(html) == "https://gluckbags.com/"


def test_detail_canonical_and_product_jsonld_without_price(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """A priceless product self-canonicalizes and emits Product + BreadcrumbList,
    but NO offers (we never fabricate a price for a 'Consultar' item)."""
    import json

    title = "Sin Precio JSONLD"
    _create_published_product_with_image(auth_client, title=title)
    pid = _product_id_by_title(app, title)

    html = client.get(f"/producto/{pid}").get_data(as_text=True)
    assert _canonical_of(html) == f"https://gluckbags.com/producto/{pid}"

    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    objs = [o for b in blocks for o in json.loads(b)]
    product = next(o for o in objs if o["@type"] == "Product")
    assert product["name"] == title
    assert product["brand"]["name"] == _BRAND
    assert "offers" not in product  # no price -> no Offer
    assert any(o["@type"] == "BreadcrumbList" for o in objs)


def test_detail_product_jsonld_has_offer_when_priced(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """A product WITH a price emits an Offer with that price + currency."""
    import io
    import json

    title = "Con Precio JSONLD"
    auth_client.post(
        "/admin/products/new",
        data={
            "title": title,
            "price": "45000",
            "is_published": "on",
            "media": (io.BytesIO(_SAMPLE_JPEG.read_bytes()), "foto.jpg"),
            "order": '["new:0"]',
        },
        content_type="multipart/form-data",
    )
    pid = _product_id_by_title(app, title)

    html = client.get(f"/producto/{pid}").get_data(as_text=True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    product = next(
        o for b in blocks for o in json.loads(b) if o["@type"] == "Product"
    )
    assert product["offers"]["price"] == "45000"
    assert product["offers"]["priceCurrency"] == "ARS"
    assert product["offers"]["availability"].endswith("InStock")


def test_home_lang_and_description(client: FlaskClient) -> None:
    """The document declares Spanish and ships a non-empty meta description."""
    html = client.get("/").get_data(as_text=True)

    assert '<html lang="es">' in html

    # Extract the description content and assert it is non-trivial (a blank
    # description silently breaks search snippets / link previews).
    match = re.search(r'<meta name="description" content="([^"]*)"', html)
    assert match is not None, "no meta description rendered"
    assert len(match.group(1).strip()) > 0


# --- 2) Favicon + critical font preloads --------------------------------------


def test_home_favicon_and_apple_touch_icon(client: FlaskClient) -> None:
    """Both the favicon and the apple-touch-icon links are present."""
    html = client.get("/").get_data(as_text=True)

    assert 'rel="icon"' in html
    assert 'rel="apple-touch-icon"' in html
    # Icons resolve to the brand avatar assets.
    assert "avatar-perfil-gluck.webp" in html
    assert "avatar-perfil-gluck.jpg" in html


def test_home_preloads_two_critical_fonts(client: FlaskClient) -> None:
    """The two above-the-fold fonts (Jost + Playfair) are preloaded as fonts."""
    html = client.get("/").get_data(as_text=True)

    # Exactly the two critical fonts are preloaded with as="font".
    font_preloads = [
        line
        for line in html.splitlines()
        if 'rel="preload"' in line and 'as="font"' in line
    ]
    assert len(font_preloads) == 2, font_preloads
    blob = "\n".join(font_preloads)
    assert "jost.woff2" in blob
    assert "playfairdisplay.woff2" in blob


def test_home_preconnects_to_nexttech(client: FlaskClient) -> None:
    """The shared Next Tech footer origin is preconnected to shave TLS setup."""
    html = client.get("/").get_data(as_text=True)

    # Assert the preconnect link itself targets the nexttech origin — not merely
    # that *a* preconnect exists and the domain appears *somewhere* (it also shows
    # up in the footer <script src>, which would make a looser check tautological).
    preconnects = re.findall(r"<link[^>]*\brel=\"preconnect\"[^>]*>", html)
    assert preconnects, "no <link rel=preconnect> rendered"
    assert any(
        'href="https://nexttech.com.ar"' in link for link in preconnects
    ), preconnects


# --- 3) Product detail OG is product-specific ---------------------------------


def test_detail_open_graph_is_product_specific(
    auth_client: FlaskClient, client: FlaskClient, app: Flask
) -> None:
    """The detail page overrides the home card: og:type=product, a product URL,
    the product title, and an og:image pointing at the cover's media variant."""
    title = "Tote Cognac"
    _create_published_product_with_image(auth_client, title=title)
    pid = _product_id_by_title(app, title)

    html = client.get(f"/producto/{pid}").get_data(as_text=True)

    # Overridden type (the home default 'website' must be gone here).
    assert _has_meta(html, key="og:type", attr="property", content="product")
    assert not _has_meta(html, key="og:type", attr="property", content="website")

    # og:url is the absolute canonical detail URL on the canonical domain.
    assert _has_meta(
        html,
        key="og:url",
        attr="property",
        content=f"https://gluckbags.com/producto/{pid}",
    )

    # Title (OG + Twitter) carries the product name.
    assert _has_meta(
        html, key="og:title", attr="property", content=f"{title} · {_BRAND}"
    )
    assert _has_meta(
        html, key="twitter:title", attr="name", content=f"{title} · {_BRAND}"
    )

    # og:image is the generated 1200x630 social crop (og.jpg), absolute, on the
    # canonical domain — not the squished vertical cover nor the static brand image.
    match = re.search(r'<meta property="og:image"\s+content="([^"]+)"', html)
    assert match is not None, "no og:image rendered"
    og_image = match.group(1)
    assert og_image.startswith("https://gluckbags.com/media/products/")
    assert og_image.endswith("/og.jpg")
    assert _has_meta(html, key="og:image:width", attr="property", content="1200")
    assert _has_meta(html, key="og:image:height", attr="property", content="630")
    # The product page never falls back to the static brand og-image.
    assert "og-image.jpg" not in html


# --- 4) Umami analytics conditional injection ---------------------------------

_UMAMI_HOST = "analytics.nexttech.com.ar"


def test_umami_absent_without_website_id(temp_app) -> None:
    """The default app has no UMAMI_WEBSITE_ID, so the tracker is NOT injected."""
    # Guard the precondition: the fixture app really has no Umami id configured.
    assert temp_app.config.get("UMAMI_WEBSITE_ID") is None

    html = temp_app.test_client().get("/").get_data(as_text=True)
    assert _UMAMI_HOST not in html
    assert "data-website-id" not in html


def test_umami_injected_when_website_id_configured(tmp_path, monkeypatch) -> None:
    """When UMAMI_WEBSITE_ID is set, the Umami script (with that id) is injected.

    create_app() reads env at construction time, so build a dedicated app with
    the id set to prove prod analytics actually ship."""
    website_id = "test-umami-id-123"
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    monkeypatch.setenv("SECRET_KEY", "k")
    monkeypatch.setenv("UMAMI_WEBSITE_ID", website_id)
    from app.factory import create_app

    application = create_app()
    application.testing = True
    assert application.config.get("UMAMI_WEBSITE_ID") == website_id

    html = application.test_client().get("/").get_data(as_text=True)
    assert f"https://{_UMAMI_HOST}/script.js" in html
    assert f'data-website-id="{website_id}"' in html
