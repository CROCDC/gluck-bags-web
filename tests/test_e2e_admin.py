"""End-to-end admin tests driving a real browser (Playwright sync API).

These exercise the full admin workflow against an isolated, HTTP-served, seeded
app (the `admin_live_server` fixture): logging in (wrong then right password),
creating a product through the JS-enhanced form (the submit goes through an XHR
that uploads the file, the server processes it, then the browser navigates), and
finally confirming the new product surfaces on the public shop grid and on its
own /producto/<id> detail page with the uploaded photo.

The admin form is enhanced by app/static/js/admin.js: on submit it builds a
FormData, POSTs via XMLHttpRequest, and on a 2xx/3xx response sets
`window.location.href = xhr.responseURL` (the redirect target, /admin/). So we
wait on URLs/selectors rather than on a classic synchronous form post.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e

# A real JPEG shipped in the repo; resolved to an absolute path from the repo
# root so set_input_files works regardless of the test runner's cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_IMAGE = os.path.abspath(
    os.path.join(_REPO_ROOT, "app", "static", "img", "productos", "crossbody-rosa.jpg")
)

# Generous default so the server-side image processing (writing several JPEG/WebP
# variants) never races the assertions on slower machines / CI.
DEFAULT_TIMEOUT_MS = 30_000


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


def _login(page: Page, base_url: str, password: str) -> None:
    """Log into the admin and land on the products list."""
    page.goto(f"{base_url}/admin/", wait_until="load")
    expect(page).to_have_url(re.compile(r"/admin/login"))
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/admin/?$"))
    expect(page.get_by_role("heading", name="Tus productos")).to_be_visible()


def _create_product_with_image(
    page: Page, base_url: str, *, title: str, price: str = "", description: str = ""
) -> None:
    """From the products list, open the new-product form, fill it, upload the
    sample image and submit. Returns once the browser is back on /admin/."""
    page.get_by_role("link", name="+ Agregar producto").click()
    page.wait_for_url(re.compile(r"/admin/products/new$"))

    page.fill("#title", title)
    if price:
        page.fill("#price", price)
    if description:
        page.fill("#description", description)

    # The input is visually hidden (class "visually-hidden"); set_input_files
    # still drives it, and admin.js mirrors the file into its internal map.
    page.set_input_files("#fileInput", SAMPLE_IMAGE)

    # Submitting kicks off the XHR upload; admin.js then sets window.location to
    # the redirect (the products list). Wait for that navigation.
    page.get_by_role("button", name="Guardar").click()
    page.wait_for_url(re.compile(r"/admin/?$"), timeout=DEFAULT_TIMEOUT_MS)


# --- tests -------------------------------------------------------------------


def test_admin_requires_login_redirect(admin_live_server: tuple[str, str], page: Page) -> None:
    """Hitting the admin index unauthenticated bounces to the login page."""
    base_url, _ = admin_live_server
    page.goto(f"{base_url}/admin/", wait_until="load")
    expect(page).to_have_url(re.compile(r"/admin/login"))
    # The login form is shown (password field + submit button).
    expect(page.locator('input[name="password"]')).to_be_visible()


def test_admin_wrong_password_shows_error(admin_live_server: tuple[str, str], page: Page) -> None:
    """A wrong password re-renders the login with the 'incorrecta' flash and
    does NOT grant access to the list."""
    base_url, _ = admin_live_server
    page.goto(f"{base_url}/admin/login", wait_until="load")
    page.fill('input[name="password"]', "definitely-not-the-password")
    page.click('button[type="submit"]')

    # Still on the login page, error flash visible.
    expect(page).to_have_url(re.compile(r"/admin/login"))
    expect(page.get_by_text(re.compile("incorrecta", re.IGNORECASE))).to_be_visible()
    # Must not have leaked the products list.
    assert "Tus productos" not in page.content()


def test_admin_correct_password_reaches_list(admin_live_server: tuple[str, str], page: Page) -> None:
    """The correct password logs in and lands on the products list."""
    base_url, password = admin_live_server
    _login(page, base_url, password)
    expect(page.get_by_role("heading", name="Tus productos")).to_be_visible()
    # The seeded app shows products, so the "+ Agregar producto" CTA is present.
    expect(page.get_by_role("link", name="+ Agregar producto")).to_be_visible()


def test_admin_create_product_full_flow(admin_live_server: tuple[str, str], page: Page) -> None:
    """The headline flow: log in, create a product with a photo, then verify it
    appears in the admin list, on the public home grid, and on its detail page
    showing the uploaded image."""
    base_url, password = admin_live_server
    title = "Cartera E2E"

    _login(page, base_url, password)
    _create_product_with_image(
        page,
        base_url,
        title=title,
        price="45000",
        description="Una cartera de prueba E2E.",
    )

    # Back on the admin list, the new product is shown.
    expect(page.get_by_role("heading", name="Tus productos")).to_be_visible()
    expect(page.get_by_text(title, exact=True)).to_be_visible()

    # --- Public home grid ---
    page.goto(f"{base_url}/", wait_until="load")
    shop_grid = page.locator(".shop-grid")
    expect(shop_grid).to_be_visible()
    product_link = shop_grid.get_by_role("link").filter(has_text=title)
    expect(product_link).to_be_visible()
    # The admin-entered price renders es-AR formatted on the card.
    expect(product_link).to_contain_text("45.000")

    # --- Detail page shows the uploaded photo ---
    href = product_link.get_attribute("href")
    assert href is not None and re.search(r"/producto/\d+", href), href
    product_link.click()
    page.wait_for_url(re.compile(r"/producto/\d+"))
    expect(page.get_by_role("heading", name=title)).to_be_visible()

    gallery_img = page.locator(".pdp-gallery img").first
    expect(gallery_img).to_be_visible()
    src = gallery_img.get_attribute("src") or ""
    # Uploaded media is served from /media/... (not the static seed images).
    assert "/media/" in src, f"expected an uploaded /media/ image, got {src!r}"

    # The image actually loaded (non-zero natural dimensions = real bytes served).
    natural_width = gallery_img.evaluate("img => img.naturalWidth")
    assert natural_width and natural_width > 0, "uploaded detail image failed to load"


def test_admin_create_unpublished_hidden_from_public(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """A product created with 'Mostrar en la web' unchecked is listed in the
    admin (as 'Oculto') but absent from the public shop grid."""
    base_url, password = admin_live_server
    title = "Cartera Oculta E2E"

    _login(page, base_url, password)
    page.get_by_role("link", name="+ Agregar producto").click()
    page.wait_for_url(re.compile(r"/admin/products/new$"))

    page.fill("#title", title)
    # Default checkbox is checked; untick it so the product stays hidden.
    checkbox = page.locator("#is_published")
    expect(checkbox).to_be_checked()
    checkbox.uncheck()
    page.set_input_files("#fileInput", SAMPLE_IMAGE)
    page.get_by_role("button", name="Guardar").click()
    page.wait_for_url(re.compile(r"/admin/?$"), timeout=DEFAULT_TIMEOUT_MS)

    # Admin list shows it, tagged "Oculto".
    row = page.locator(".adm-row").filter(has_text=title)
    expect(row).to_be_visible()
    expect(row.get_by_text("Oculto")).to_be_visible()

    # Public home does NOT show it.
    page.goto(f"{base_url}/", wait_until="load")
    assert title not in page.content()


# --- edit / toggle / delete / logout -----------------------------------------


def test_admin_logout_ends_session(admin_live_server: tuple[str, str], page: Page) -> None:
    """Clicking 'Salir' clears the session: the admin index bounces to login again."""
    base_url, password = admin_live_server
    _login(page, base_url, password)

    page.get_by_role("button", name="Salir").click()
    page.wait_for_url(re.compile(r"/admin/login"))

    # The session is really gone, not just the current page: re-entering /admin/
    # redirects back to the login form.
    page.goto(f"{base_url}/admin/", wait_until="load")
    expect(page).to_have_url(re.compile(r"/admin/login"))
    expect(page.locator('input[name="password"]')).to_be_visible()


def test_admin_edit_product_updates_admin_and_public(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Editing a product's title + price from the UI is reflected in the admin
    list and on the public shop grid (the existing photo is kept)."""
    base_url, password = admin_live_server
    original = "Cartera Editar E2E"
    updated = "Cartera Editada E2E"

    _login(page, base_url, password)
    _create_product_with_image(page, base_url, title=original, price="30000")

    # Open the edit form from the product's row.
    page.locator(".adm-row").filter(has_text=original).get_by_role(
        "link", name="Editar"
    ).click()
    page.wait_for_url(re.compile(r"/admin/products/\d+/edit$"))
    expect(page.locator("#title")).to_have_value(original)

    page.fill("#title", updated)
    page.fill("#price", "55000")
    page.get_by_role("button", name="Guardar").click()
    page.wait_for_url(re.compile(r"/admin/?$"), timeout=DEFAULT_TIMEOUT_MS)

    # Admin list shows the new title and no longer the old one.
    expect(page.get_by_text(updated, exact=True)).to_be_visible()
    expect(page.get_by_text(original, exact=True)).to_have_count(0)

    # Public shop grid reflects the edited title + price.
    page.goto(f"{base_url}/", wait_until="load")
    card = page.locator(".shop-grid").get_by_role("link").filter(has_text=updated)
    expect(card).to_be_visible()
    expect(card).to_contain_text("55.000")


def test_admin_toggle_hides_product_from_public(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Toggling a published product to hidden marks it 'Oculto' in the admin and
    removes it from the public shop grid."""
    base_url, password = admin_live_server
    title = "Cartera Toggle E2E"

    _login(page, base_url, password)
    _create_product_with_image(page, base_url, title=title)  # published by default

    # Visible publicly right after creation.
    page.goto(f"{base_url}/", wait_until="load")
    expect(
        page.locator(".shop-grid").get_by_role("link").filter(has_text=title)
    ).to_be_visible()

    # Hide it from the admin list (the 👁 button, accessible name "Ocultar de la web").
    page.goto(f"{base_url}/admin/", wait_until="load")
    row = page.locator(".adm-row").filter(has_text=title)
    expect(row.get_by_text("Visible")).to_be_visible()
    row.get_by_role("button", name="Ocultar de la web").click()
    page.wait_for_url(re.compile(r"/admin/?$"))

    # Now tagged 'Oculto' in the admin...
    expect(
        page.locator(".adm-row").filter(has_text=title).get_by_text("Oculto")
    ).to_be_visible()
    # ...and absent from the public home.
    page.goto(f"{base_url}/", wait_until="load")
    assert title not in page.content()


def test_admin_delete_product_removes_it_everywhere(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Deleting a product from the admin (accepting the confirm dialog) removes it
    from the admin list and the public site; its detail page then 404s."""
    base_url, password = admin_live_server
    title = "Cartera Borrar E2E"

    _login(page, base_url, password)
    _create_product_with_image(page, base_url, title=title)

    # Capture its public detail URL before deleting, to assert the 404 afterwards.
    page.goto(f"{base_url}/", wait_until="load")
    href = (
        page.locator(".shop-grid")
        .get_by_role("link")
        .filter(has_text=title)
        .get_attribute("href")
    )
    assert href and re.search(r"/producto/\d+", href), href

    # Delete from the admin list. The delete button fires a JS confirm(); Playwright
    # auto-DISMISSES dialogs by default (which would cancel the POST), so accept it.
    page.goto(f"{base_url}/admin/", wait_until="load")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(".adm-row").filter(has_text=title).get_by_role(
        "button", name="Borrar"
    ).click()
    page.wait_for_url(re.compile(r"/admin/?$"))

    # Gone from the admin list...
    expect(page.get_by_text(title, exact=True)).to_have_count(0)
    # ...and from the public home, and the detail page now 404s.
    page.goto(f"{base_url}/", wait_until="load")
    assert title not in page.content()
    response = page.goto(f"{base_url}{href}", wait_until="load")
    assert response is not None and response.status == 404
