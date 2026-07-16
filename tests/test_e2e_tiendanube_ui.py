"""End-to-end UI + acceptance tests for the NEW storefront served from the Tienda
Nube mirror (CATALOG_SOURCE=tiendanube).

Drives a real browser against an isolated app whose storefront comes entirely from
the mirrored TN catalogue (no admin products). Covers the acceptance criteria of the
swap:

1. the home grid renders the TN catalogue (title + price),
2. the product page's "Agregar al carrito" adds by **TN id**, bumps the badge and
   opens the drawer with the line (the loop's UI, driven by cart.js),
3. the /carrito page lists the item with the right subtotal,
4. checkout degrades gracefully when TN payment isn't configured (feedback, no crash),
5. /gracias renders and clears the cart,
6. none of the new pages overflow horizontally at a phone width.

Images point at a real static asset so layout (and the overflow probe) is faithful.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import tempfile
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect
from werkzeug.serving import make_server

pytestmark = pytest.mark.e2e

DEFAULT_TIMEOUT_MS = 30_000
PHONE_VIEWPORT = {"width": 390, "height": 800}
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _seed_payloads() -> list[dict[str, Any]]:
    """Two published, purchasable TN products with a real, same-origin image."""

    def mk(tn_id: int, name: str, price: int, category: str, img_src: str = "/static/img/og-image.jpg") -> dict[str, Any]:
        return {
            "id": tn_id,
            "name": {"es": name},
            "description": {"es": f"{name}: bolso de cuero vegano hecho a mano."},
            "handle": {"es": name.lower().replace(" ", "-")},
            "published": True,
            "canonical_url": f"https://gluck29.mitiendanube.com/productos/{tn_id}/",
            "categories": [{"id": 1, "name": {"es": category}}],
            "variants": [{"id": tn_id * 10, "price": f"{price}.00", "stock": 8}],
            # Same-origin real asset so <img> loads and dimensions reserve space.
            "images": [{"id": tn_id * 100, "src": img_src, "position": 1, "width": 1200, "height": 630}],
        }

    gallery = mk(104, "Tote Galeria", 52000, "Tote")
    gallery["description"] = {"es": "<p>Cartera tipo tote color suela.</p><p>Cuero vegano.</p>"}
    gallery["images"] = [
        {"id": 104001, "src": "/static/img/og-image.jpg", "position": 1, "width": 1200, "height": 630},
        {"id": 104002, "src": "/static/img/productos/tote-cognac-01.jpg", "position": 2, "width": 1080, "height": 1350},
        {"id": 104003, "src": "/static/img/productos/tote-gris-interior.jpg", "position": 3, "width": 1080, "height": 1350},
    ]

    return [
        mk(101, "Tote Cognac", 45000, "Tote"),
        mk(102, "Mini Rosa", 30000, "Mini Bag"),
        # A mirrored product whose TN image src is an attribute-breaking XSS payload
        # (invalid URL so a pre-fix drawer would fire the injected onerror). Exercises
        # the escaping in cart.js lineHTML.
        mk(103, "XSS Probe", 15000, "Tote", img_src='x" onerror="window.__xss=1"'),
        # Multi-image product with a rich-text (HTML) TN description: drives the
        # snap-scroll gallery + thumbs and the html-to-text description path.
        gallery,
    ]


@pytest.fixture(scope="module")
def tn_live_server() -> Iterator[str]:
    """An isolated app served over HTTP with the storefront on the TN mirror."""
    tmp = tempfile.mkdtemp(prefix="gluck-tn-e2e-")
    saved = {
        k: os.environ.get(k)
        for k in ("DATA_DIR", "SEED_PRODUCTS", "CATALOG_SOURCE", "ADMIN_PASSWORD", "SECRET_KEY")
    }
    os.environ["DATA_DIR"] = tmp
    os.environ["SEED_PRODUCTS"] = "0"  # storefront comes from the mirror, not admin
    os.environ["CATALOG_SOURCE"] = "tiendanube"
    os.environ["ADMIN_PASSWORD"] = "e2e-pw"
    os.environ["SECRET_KEY"] = "test-secret-key"
    from app.factory import create_app, db
    from app.models import TiendaNubeProduct

    app = create_app()
    with app.app_context():
        for payload in _seed_payloads():
            db.session.add(TiendaNubeProduct(tn_id=payload["id"]).apply_payload(payload))
        db.session.commit()

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context()
    pg = context.new_page()
    pg.set_default_timeout(DEFAULT_TIMEOUT_MS)
    pg.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
    try:
        yield pg
    finally:
        context.close()


# --- 1. home renders the mirror ----------------------------------------------


def test_home_shows_tn_catalogue(tn_live_server: str, page: Page) -> None:
    page.goto(f"{tn_live_server}/", wait_until="load")
    grid = page.locator(".shop-grid")
    expect(grid).to_be_visible()
    expect(grid.get_by_text("Tote Cognac")).to_be_visible()
    expect(grid.get_by_text("Mini Rosa")).to_be_visible()
    expect(grid.get_by_text("$ 45.000")).to_be_visible()


# --- 2. add to cart (by TN id) -> badge + drawer -----------------------------


def test_add_to_cart_bumps_badge_and_opens_drawer(tn_live_server: str, page: Page) -> None:
    page.goto(f"{tn_live_server}/producto/101", wait_until="load")
    # The CTA carries the TN product id — proving the cart line will be a TN id.
    add = page.locator('[data-add-to-cart="101"]')
    expect(add).to_be_visible()
    add.click()

    badge = page.locator("[data-cart-count]").first
    expect(badge).to_have_text("1")

    drawer = page.locator("#cartDrawer")
    expect(drawer).to_have_class(re.compile(r"\bopen\b"))
    # The drawer line is keyed by the TN id.
    expect(drawer.locator('[data-cart-line="101"]')).to_be_visible()
    expect(drawer.get_by_text("Tote Cognac")).to_be_visible()


# --- 3. /carrito lists the item + subtotal -----------------------------------


def test_cart_page_lists_item_and_subtotal(tn_live_server: str, page: Page) -> None:
    page.goto(f"{tn_live_server}/producto/102", wait_until="load")
    page.locator('[data-add-to-cart="102"]').click()
    page.wait_for_function("() => document.querySelector('[data-cart-count]').textContent === '1'")

    page.goto(f"{tn_live_server}/carrito", wait_until="load")
    expect(page.get_by_text("Mini Rosa")).to_be_visible()
    subtotal = page.locator("[data-cart-subtotal]").first
    expect(subtotal).to_have_text("$ 30.000")


# --- 4. checkout degrades gracefully (no TN payment configured) --------------


def test_checkout_requires_email_before_handoff(tn_live_server: str, page: Page) -> None:
    """Clicking "Finalizar compra" without an email never leaves the page: the email
    is what TN prefills at its hosted checkout, so the UI gates on it client-side."""
    page.goto(f"{tn_live_server}/producto/101", wait_until="load")
    page.locator('[data-add-to-cart="101"]').click()
    page.wait_for_function("() => document.querySelector('[data-cart-count]').textContent === '1'")

    page.goto(f"{tn_live_server}/carrito", wait_until="load")
    page.locator("[data-cart-checkout]").first.click()
    feedback = page.locator("[data-cart-feedback]").first
    expect(feedback).to_be_visible()
    expect(feedback).to_contain_text("email")
    expect(page).to_have_url(re.compile(r"/carrito$"))


def test_checkout_without_credentials_shows_feedback(tn_live_server: str, page: Page) -> None:
    page.goto(f"{tn_live_server}/producto/101", wait_until="load")
    page.locator('[data-add-to-cart="101"]').click()
    page.wait_for_function("() => document.querySelector('[data-cart-count]').textContent === '1'")

    page.goto(f"{tn_live_server}/carrito", wait_until="load")
    page.locator(".cart-summary [data-checkout-email]").fill("ana@example.com")
    page.locator("[data-cart-checkout]").first.click()
    # No token in this env -> not_configured -> a visible message, never a crash
    # and never a redirect off-site.
    feedback = page.locator("[data-cart-feedback]").first
    expect(feedback).to_be_visible()
    expect(page).to_have_url(re.compile(r"/carrito$"))


# --- 3b. PDP gallery: thumbs drive the snap track; description is clean text ---


def test_pdp_gallery_thumbs_navigate_slides(tn_live_server: str, page: Page) -> None:
    page.goto(f"{tn_live_server}/producto/104", wait_until="load")

    slides = page.locator(".pdp-gallery .pdp-media")
    expect(slides).to_have_count(3)
    thumbs = page.locator("[data-gallery-thumb]")
    expect(thumbs).to_have_count(3)
    expect(thumbs.nth(0)).to_have_class(re.compile("is-current"))

    thumbs.nth(2).click()
    page.wait_for_function(
        "() => document.querySelector('[data-gallery]').scrollLeft > 0"
    )
    expect(thumbs.nth(2)).to_have_class(re.compile("is-current"))


def test_pdp_gallery_autoplays(tn_live_server: str, page: Page) -> None:
    """Untouched, the gallery advances to the next slide on its own (5s cadence)."""
    page.goto(f"{tn_live_server}/producto/104", wait_until="load")
    track = page.locator("[data-gallery]")
    assert page.evaluate("() => document.querySelector('[data-gallery]').scrollLeft") == 0
    page.wait_for_function(
        "() => document.querySelector('[data-gallery]').scrollLeft > 0", timeout=8_000
    )
    expect(page.locator("[data-gallery-thumb]").nth(1)).to_have_class(re.compile("is-current"))


def test_pdp_gallery_autoplay_stops_after_interaction(tn_live_server: str, page: Page) -> None:
    """One interaction (a thumb click) hands control to the buyer for good."""
    page.goto(f"{tn_live_server}/producto/104", wait_until="load")
    page.locator("[data-gallery-thumb]").nth(0).click()
    page.wait_for_timeout(6_500)
    assert page.evaluate("() => document.querySelector('[data-gallery]').scrollLeft") == 0
    expect(page.locator("[data-gallery-thumb]").nth(0)).to_have_class(re.compile("is-current"))


def test_pdp_gallery_respects_reduced_motion(tn_live_server: str, browser: Browser) -> None:
    context = browser.new_context(reduced_motion="reduce")
    pg = context.new_page()
    try:
        pg.goto(f"{tn_live_server}/producto/104", wait_until="load")
        pg.wait_for_timeout(6_500)
        assert pg.evaluate("() => document.querySelector('[data-gallery]').scrollLeft") == 0
    finally:
        context.close()


def test_pdp_single_image_has_no_thumbs(tn_live_server: str, page: Page) -> None:
    page.goto(f"{tn_live_server}/producto/101", wait_until="load")
    expect(page.locator(".pdp-gallery .pdp-media")).to_have_count(1)
    assert page.locator(".pdp-thumbs").count() == 0


def test_pdp_description_renders_clean_paragraphs(tn_live_server: str, page: Page) -> None:
    page.goto(f"{tn_live_server}/producto/104", wait_until="load")
    desc = page.locator(".pdp-desc")
    expect(desc).to_contain_text("Cartera tipo tote color suela.")
    assert "<p>" not in (desc.text_content() or "")


# --- 4b. the drawer escapes an attribute-breaking image src (stored-XSS guard) ---


def test_drawer_escapes_malicious_image_src(tn_live_server: str, page: Page) -> None:
    """A TN-mirrored product whose image src is `x" onerror="..."` must NOT inject an
    onerror handler when cart.js builds the drawer HTML. Guards the escaping in
    lineHTML (the server-rendered cart.html is already safe via Jinja)."""
    page.goto(f"{tn_live_server}/producto/103", wait_until="load")
    page.locator('[data-add-to-cart="103"]').click()
    page.wait_for_function("() => document.querySelector('[data-cart-count]').textContent === '1'")

    # The payload's `onerror` must not have survived as a real attribute...
    assert page.locator("#cartDrawer img[onerror]").count() == 0
    # ...and must not have executed (the img src is invalid, so a raw injection would
    # have fired onerror by now).
    assert page.evaluate("() => window.__xss") is None


# --- 5. /gracias renders and clears the cart ---------------------------------


def test_gracias_renders_and_preserves_unrelated_cart(tn_live_server: str, page: Page) -> None:
    """Without a pending TN handoff (this session never checked out), /gracias must
    NOT touch the in-progress cart — the URL is publicly reachable."""
    page.goto(f"{tn_live_server}/producto/101", wait_until="load")
    page.locator('[data-add-to-cart="101"]').click()
    page.wait_for_function("() => document.querySelector('[data-cart-count]').textContent === '1'")

    page.goto(f"{tn_live_server}/gracias", wait_until="load")
    expect(page.get_by_role("heading", name="¡Gracias por tu compra!")).to_be_visible()

    # The visitor's cart survives: the badge still shows the item afterwards.
    page.goto(f"{tn_live_server}/", wait_until="load")
    badge = page.locator("[data-cart-count]").first
    assert badge.text_content() == "1"


# --- 6. no horizontal overflow on the new pages ------------------------------
# Probe the same STRUCTURAL containers the project's responsive layer uses (not
# every leaf), so an intentional full-bleed/animated element — the marquee track,
# the hero picture — doesn't trip the guard, but a layout container that TN data
# pushed past the viewport does. Curated per page; the assertion also requires the
# page's own container to actually resolve, so it can't pass vacuously.

from pages import HOME_CONTAINERS, PDP_CONTAINERS, PUBLIC_CONTAINERS  # noqa: E402

_CATEGORY_CONTAINERS = PUBLIC_CONTAINERS + [".shop-grid", ".shop-grid .product"]
_CART_CONTAINERS = PUBLIC_CONTAINERS + [".cart-page", ".cart-page-grid", ".cart-line", ".cart-summary"]
_GRACIAS_CONTAINERS = PUBLIC_CONTAINERS + [".cart-page", ".cart-empty"]

_OVERFLOW_JS = """
(selectors) => {
  let max = 0, seen = 0;
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden') continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      seen += 1;
      if (r.right > max) max = r.right;
    }
  }
  return { max, seen };
}
"""

_OVERFLOW_CASES = [
    ("/", HOME_CONTAINERS, ".shop-grid .product"),
    ("/producto/101", PDP_CONTAINERS, ".pdp-grid"),
    ("/categoria/tote", _CATEGORY_CONTAINERS, ".shop-grid .product"),
    ("/carrito", _CART_CONTAINERS, ".cart-line"),
    ("/gracias", _GRACIAS_CONTAINERS, ".cart-empty"),
]


@pytest.mark.parametrize("path, containers, must_resolve", _OVERFLOW_CASES)
def test_no_horizontal_overflow_on_phone(
    tn_live_server: str, page: Page, path: str, containers: list, must_resolve: str
) -> None:
    page.set_viewport_size(PHONE_VIEWPORT)
    # Carrito/gracias need an item first (add via the API to seed the session cart).
    if path in ("/carrito",):
        page.goto(f"{tn_live_server}/producto/101", wait_until="load")
        page.locator('[data-add-to-cart="101"]').click()
        page.wait_for_function("() => document.querySelector('[data-cart-count]').textContent === '1'")
    page.goto(f"{tn_live_server}{path}", wait_until="load")

    # Guard against a vacuous pass: the page's signature container must be present.
    assert page.locator(must_resolve).count() > 0, f"{path}: {must_resolve} did not resolve"

    result = page.evaluate(_OVERFLOW_JS, containers)
    assert result["seen"] > 0, f"{path}: no structural containers resolved"
    assert result["max"] <= PHONE_VIEWPORT["width"] + 1, f"{path} overflows: max right {result['max']}px"
