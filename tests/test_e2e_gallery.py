"""End-to-end browser tests for the admin media-gallery uploader (admin.js).

The gallery editor in app/static/js/admin.js is the owner's daily workflow:
queue several photos at once, pick a cover with the ★ button, drop a tile with
the × button, reorder by dragging the ⋮⋮ handle. Existing coverage only POSTs
the `order` field directly — it never drives the real JS that BUILDS that field.
These tests do, and assert each edit through to what the public sees:

  - the detail page renders `product.media` in position order, cover first, so
    `.pdp-gallery img` count + order reflect the queued/removed tiles, and
  - the home grid card uses `product.cover` (the is_cover item), so its image
    must match the promoted tile.

Cover/order is proven via aspect ratio: we generate a clearly PORTRAIT and a
clearly LANDSCAPE JPEG, then read naturalWidth/naturalHeight off the served
image (every responsive variant preserves the original ratio).
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterator

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e

DEFAULT_TIMEOUT_MS = 30_000

# A real JPEG shipped in the repo, for tests that don't care WHICH image is which.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_IMAGE = os.path.abspath(
    os.path.join(_REPO_ROOT, "app", "static", "img", "productos", "crossbody-rosa.jpg")
)


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    """A fresh, isolated browser context+page per test (no shared cookies/cache)."""
    context = browser.new_context()
    pg = context.new_page()
    pg.set_default_timeout(DEFAULT_TIMEOUT_MS)
    pg.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
    try:
        yield pg
    finally:
        context.close()


# --- helpers -----------------------------------------------------------------


def _login(page: Page, base_url: str, password: str) -> None:
    page.goto(f"{base_url}/admin/", wait_until="load")
    expect(page).to_have_url(re.compile(r"/admin/login"))
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/admin/?$"))
    expect(page.get_by_role("heading", name="Tus productos")).to_be_visible()


def _make_jpeg(directory: str, name: str, size: tuple[int, int], color: tuple[int, int, int]) -> str:
    """Write a solid-color JPEG and return its absolute path. A distinct color per
    image keeps the bytes different so the browser can't dedupe them."""
    path = os.path.join(directory, name)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


def _open_new_form(page: Page, base_url: str, title: str) -> None:
    page.get_by_role("link", name="+ Agregar producto").click()
    page.wait_for_url(re.compile(r"/admin/products/new$"))
    page.fill("#title", title)


def _submit_form(page: Page) -> None:
    """Click Guardar and wait for admin.js to navigate back to the list after the
    XHR upload + server-side image processing complete."""
    page.get_by_role("button", name="Guardar").click()
    page.wait_for_url(re.compile(r"/admin/?$"), timeout=DEFAULT_TIMEOUT_MS)


def _open_detail(page: Page, base_url: str, title: str) -> None:
    """Navigate from the public home grid to the product's detail page."""
    page.goto(f"{base_url}/", wait_until="load")
    link = page.locator(".shop-grid").get_by_role("link").filter(has_text=title)
    expect(link).to_be_visible()
    link.click()
    page.wait_for_url(re.compile(r"/producto/\d+"))
    expect(page.get_by_role("heading", name=title)).to_be_visible()


def _natural_ratio(img_locator) -> float:
    """naturalWidth / naturalHeight of a loaded <img>; >1 landscape, <1 portrait.
    Asserts the image actually loaded (non-zero bytes)."""
    dims = img_locator.evaluate(
        "img => ({w: img.naturalWidth, h: img.naturalHeight})"
    )
    assert dims["w"] > 0 and dims["h"] > 0, f"image did not load: {dims}"
    return dims["w"] / dims["h"]


def _lazy_natural_ratio(img_locator) -> float:
    """Like _natural_ratio but for a loading="lazy" image below the fold: scroll
    it into view and wait for it to actually decode before reading dimensions."""
    img_locator.scroll_into_view_if_needed()
    img_locator.evaluate(
        "img => img.complete && img.naturalWidth > 0 ? null : img.decode()"
    )
    return _natural_ratio(img_locator)


# Portrait (tall) vs landscape (wide): ratios sit clearly on opposite sides of 1.
_PORTRAIT = (480, 640)   # ratio 0.75
_LANDSCAPE = (640, 360)  # ratio ~1.78


# --- 1) multi-image upload ---------------------------------------------------


def test_multi_image_upload_shows_all_three(admin_live_server, page: Page) -> None:
    """Queueing 3 images at once builds order=[new:0,new:1,new:2]; the detail page
    then shows 3 gallery images, all of which load."""
    base_url, password = admin_live_server
    title = "Galeria Triple E2E"

    with tempfile.TemporaryDirectory() as tmp:
        imgs = [
            _make_jpeg(tmp, "a.jpg", (600, 600), (200, 30, 30)),
            _make_jpeg(tmp, "b.jpg", (600, 600), (30, 200, 30)),
            _make_jpeg(tmp, "c.jpg", (600, 600), (30, 30, 200)),
        ]
        _login(page, base_url, password)
        _open_new_form(page, base_url, title)
        # A single set_input_files with a list queues all three through one change
        # event, so admin.js assigns them tokens new:0, new:1, new:2 in order.
        page.set_input_files("#fileInput", imgs)
        expect(page.locator("#tiles .tile")).to_have_count(3)
        _submit_form(page)

    _open_detail(page, base_url, title)
    gallery = page.locator(".pdp-gallery img")
    expect(gallery).to_have_count(3)
    for i in range(3):
        # Each renders from /media/ and decodes to real pixels (later tiles are
        # loading="lazy", so scroll+decode them before reading dimensions).
        src = gallery.nth(i).get_attribute("src") or ""
        assert "/media/" in src, f"tile {i} not served from /media/: {src!r}"
        assert _lazy_natural_ratio(gallery.nth(i)) > 0


# --- 2) make-cover (★) -------------------------------------------------------


def test_make_cover_promotes_second_tile(admin_live_server, page: Page) -> None:
    """Queue portrait then landscape, click ★ on the SECOND tile so it jumps to
    first. The detail page's first image and the home card cover must then be the
    LANDSCAPE one (ratio > 1) — proving the cover reorder went end to end."""
    base_url, password = admin_live_server
    title = "Portada Estrella E2E"

    with tempfile.TemporaryDirectory() as tmp:
        portrait = _make_jpeg(tmp, "portrait.jpg", _PORTRAIT, (120, 60, 60))
        landscape = _make_jpeg(tmp, "landscape.jpg", _LANDSCAPE, (60, 60, 120))
        _login(page, base_url, password)
        _open_new_form(page, base_url, title)
        page.set_input_files("#fileInput", [portrait, landscape])
        expect(page.locator("#tiles .tile")).to_have_count(2)

        # Promote the second tile (landscape) to cover via its ★ button.
        page.locator("#tiles .tile").nth(1).locator(".tile-makecover").click()
        # admin.js moves it to the front of the list immediately.
        expect(page.locator("#tiles .tile").first.locator(".tile-name")).to_have_text(
            "landscape.jpg"
        )
        _submit_form(page)

    # Detail page: first gallery image is now the landscape (cover) one.
    _open_detail(page, base_url, title)
    gallery = page.locator(".pdp-gallery img")
    expect(gallery).to_have_count(2)
    assert _natural_ratio(gallery.first) > 1, "detail cover is not the landscape image"

    # Home grid card cover (product.cover = the is_cover item) must match too.
    page.goto(f"{base_url}/", wait_until="load")
    card_img = (
        page.locator(".shop-grid")
        .get_by_role("link")
        .filter(has_text=title)
        .locator("img")
        .first
    )
    assert _lazy_natural_ratio(card_img) > 1, "home card cover is not the landscape image"


# --- 3) remove a queued NEW tile (× , no confirm) ----------------------------


def test_remove_queued_new_tile_leaves_one(admin_live_server, page: Page) -> None:
    """Removing a new:* tile fires NO confirm dialog and drops that file from the
    upload, so the detail page shows exactly one image."""
    base_url, password = admin_live_server
    title = "Quitar Nueva E2E"

    # If a confirm() ever appeared here it would be a regression; record any dialog.
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

    with tempfile.TemporaryDirectory() as tmp:
        imgs = [
            _make_jpeg(tmp, "keep.jpg", (600, 600), (200, 30, 30)),
            _make_jpeg(tmp, "drop.jpg", (600, 600), (30, 30, 200)),
        ]
        _login(page, base_url, password)
        _open_new_form(page, base_url, title)
        page.set_input_files("#fileInput", imgs)
        expect(page.locator("#tiles .tile")).to_have_count(2)

        # Remove the second tile via ×. New tiles have no confirm guard.
        page.locator("#tiles .tile").nth(1).locator(".tile-remove").click()
        expect(page.locator("#tiles .tile")).to_have_count(1)
        _submit_form(page)

    assert dialogs == [], f"removing a NEW tile should not confirm; got {dialogs}"

    _open_detail(page, base_url, title)
    expect(page.locator(".pdp-gallery img")).to_have_count(1)


# --- 4) remove an EXISTING tile (× fires confirm) ----------------------------


def test_remove_existing_tile_fires_confirm(admin_live_server, page: Page) -> None:
    """On the Edit form, an existing:* tile's × fires window.confirm. Accepting it
    removes the tile; saving drops that media so the detail page shows one image."""
    base_url, password = admin_live_server
    title = "Quitar Existente E2E"

    with tempfile.TemporaryDirectory() as tmp:
        imgs = [
            _make_jpeg(tmp, "one.jpg", (600, 600), (200, 30, 30)),
            _make_jpeg(tmp, "two.jpg", (600, 600), (30, 30, 200)),
        ]
        _login(page, base_url, password)
        _open_new_form(page, base_url, title)
        page.set_input_files("#fileInput", imgs)
        expect(page.locator("#tiles .tile")).to_have_count(2)
        _submit_form(page)

    # Open the Edit form: both media render as existing:* tiles.
    page.locator(".adm-row").filter(has_text=title).get_by_role("link", name="Editar").click()
    page.wait_for_url(re.compile(r"/admin/products/\d+/edit$"))
    existing_tiles = page.locator('#tiles .tile[data-token^="existing:"]')
    expect(existing_tiles).to_have_count(2)

    # Removing an existing tile must prompt; capture that the dialog actually fired.
    confirmed: list[str] = []
    page.once("dialog", lambda d: (confirmed.append(d.message), d.accept()))
    existing_tiles.first.locator(".tile-remove").click()
    expect(page.locator("#tiles .tile")).to_have_count(1)
    assert confirmed, "removing an existing tile should have fired window.confirm"

    _submit_form(page)

    _open_detail(page, base_url, title)
    expect(page.locator(".pdp-gallery img")).to_have_count(1)


# --- 5) edit keeps existing + adds new ---------------------------------------


def test_edit_keeps_existing_and_adds_new(admin_live_server, page: Page) -> None:
    """Editing a 1-image product: the existing tile renders, we add one new file,
    save, and the detail page then shows two images (kept + added)."""
    base_url, password = admin_live_server
    title = "Sumar Foto E2E"

    _login(page, base_url, password)
    _open_new_form(page, base_url, title)
    page.set_input_files("#fileInput", SAMPLE_IMAGE)
    expect(page.locator("#tiles .tile")).to_have_count(1)
    _submit_form(page)

    page.locator(".adm-row").filter(has_text=title).get_by_role("link", name="Editar").click()
    page.wait_for_url(re.compile(r"/admin/products/\d+/edit$"))
    expect(page.locator('#tiles .tile[data-token^="existing:"]')).to_have_count(1)

    with tempfile.TemporaryDirectory() as tmp:
        extra = _make_jpeg(tmp, "extra.jpg", (600, 600), (30, 200, 30))
        page.set_input_files("#fileInput", extra)
        # Now: one existing tile + one new tile.
        expect(page.locator("#tiles .tile")).to_have_count(2)
        _submit_form(page)

    _open_detail(page, base_url, title)
    expect(page.locator(".pdp-gallery img")).to_have_count(2)


# --- 6) drag-reorder tiles ---------------------------------------------------


def test_drag_reorder_swaps_cover(admin_live_server, page: Page) -> None:
    """Drag the 2nd tile above the 1st via its ⋮⋮ handle (Pointer-Events sortable
    needs intermediate moves crossing the first tile's midpoint). The detail
    page's first image must then be the dragged (landscape) one."""
    base_url, password = admin_live_server
    title = "Arrastrar Orden E2E"

    with tempfile.TemporaryDirectory() as tmp:
        portrait = _make_jpeg(tmp, "portrait.jpg", _PORTRAIT, (120, 60, 60))
        landscape = _make_jpeg(tmp, "landscape.jpg", _LANDSCAPE, (60, 60, 120))
        _login(page, base_url, password)
        _open_new_form(page, base_url, title)
        page.set_input_files("#fileInput", [portrait, landscape])
        expect(page.locator("#tiles .tile")).to_have_count(2)

        # page.mouse dispatches at literal viewport coordinates and does NOT
        # auto-scroll, so the tiles must be on-screen first or the events miss them.
        page.locator("#tiles").scroll_into_view_if_needed()

        second_handle = page.locator("#tiles .tile").nth(1).locator(".tile-handle")
        first_box = page.locator("#tiles .tile").first.bounding_box()
        src_box = second_handle.bounding_box()
        assert first_box and src_box

        # Pointer-Events sortable: press on the 2nd handle, move up in steps that
        # cross the 1st tile's vertical midpoint, then release. Several moves are
        # required (pointermove drives the reorder; a single jump won't trigger it).
        start_y = src_box["y"] + src_box["height"] / 2
        x = src_box["x"] + src_box["width"] / 2
        page.mouse.move(x, start_y)
        page.mouse.down()
        target_y = first_box["y"] - 10  # clearly above the 1st tile's midpoint
        steps = 12
        for i in range(1, steps + 1):
            y = start_y + (target_y - start_y) * i / steps
            page.mouse.move(x, y)
        page.mouse.up()

        # The landscape tile should now be first in the list before we submit.
        expect(page.locator("#tiles .tile").first.locator(".tile-name")).to_have_text(
            "landscape.jpg"
        )
        _submit_form(page)

    _open_detail(page, base_url, title)
    gallery = page.locator(".pdp-gallery img")
    expect(gallery).to_have_count(2)
    assert _natural_ratio(gallery.first) > 1, "drag did not move the landscape image to first"
