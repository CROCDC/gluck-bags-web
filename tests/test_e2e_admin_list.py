"""End-to-end browser test for the admin product-list drag-to-reorder.

Covers the gap the other E2E suite misses: app/static/js/admin.js wires the
products list (#admList) as a Pointer-Events sortable (makeSortable). Dragging a
row by its .adm-handle fires a fetch POST of {order:[ids...]} to the list's
data-reorder-url (/admin/products/reorder); the server persists the new
`position` per product. That ordering drives both the admin list and the public
shop grid (ProductRepository orders by position, then id).

So this file logs in, creates three products A, B, C (each with one image),
drags row C above row A, waits for the reorder POST to land, then asserts the
new order persists in BOTH places: the admin list and the public home grid now
lead with C before A.

The sortable listens to Pointer Events (pointerdown on the handle, pointermove,
pointerup). Playwright's page.mouse synthesizes those, so we drive the drag with
small intermediate mouse.move steps past A's vertical midpoint, and require that
the genuine gesture (a) reorders the DOM and (b) fires a real reorder POST. There
is intentionally NO fetch-fallback that keeps the test green: hand-issuing the
reorder request would pass even if makeSortable were unwired, masking exactly the
regression this file exists to catch. If the synthesized drag ever fails to fire
the POST, the test FAILS — and as a diagnostic it then pokes the reorder route
directly to point the finger at the JS sortable vs. the backend.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e

# A real JPEG shipped in the repo; absolute so set_input_files is cwd-independent.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_IMAGE = os.path.abspath(
    os.path.join(_REPO_ROOT, "app", "static", "img", "productos", "crossbody-rosa.jpg")
)

# Generous: each create writes several JPEG/WebP variants server-side.
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


def _create_product_with_image(page: Page, base_url: str, *, title: str) -> None:
    """Open the new-product form, fill the title, upload the sample image and
    submit. Returns once the browser is back on /admin/ (admin.js navigates to
    xhr.responseURL after the upload is processed)."""
    page.get_by_role("link", name="+ Agregar producto").click()
    page.wait_for_url(re.compile(r"/admin/products/new$"))
    page.fill("#title", title)
    # #fileInput is visually hidden; set_input_files still drives it and admin.js
    # mirrors the file into its internal map.
    page.set_input_files("#fileInput", SAMPLE_IMAGE)
    page.get_by_role("button", name="Guardar").click()
    page.wait_for_url(re.compile(r"/admin/?$"), timeout=DEFAULT_TIMEOUT_MS)


def _row_titles(page: Page) -> list[str]:
    """The product titles in current DOM order of the admin list."""
    return [
        (t or "").strip()
        for t in page.locator("#admList .adm-row .adm-row-title").all_inner_texts()
    ]


def _row_ids(page: Page) -> list[str]:
    """The data-id attributes in current DOM order of the admin list."""
    return page.locator("#admList .adm-row").evaluate_all(
        "rows => rows.map(r => r.dataset.id)"
    )


def _drag_row_above(page: Page, *, drag_id: str, target_id: str) -> bool:
    """Drag the row with data-id=drag_id by its handle until it sits above the row
    with data-id=target_id, using small Pointer-Events mouse steps. Returns True
    if the in-DOM order changed (the sortable reacted); False otherwise."""
    handle = page.locator(f'#admList .adm-row[data-id="{drag_id}"] .adm-handle')
    handle.scroll_into_view_if_needed()
    h_box = handle.bounding_box()
    target_box = page.locator(f'#admList .adm-row[data-id="{target_id}"]').bounding_box()
    assert h_box is not None and target_box is not None

    start_x = h_box["x"] + h_box["width"] / 2
    start_y = h_box["y"] + h_box["height"] / 2
    # Drop point: comfortably ABOVE the target row's vertical midpoint so
    # getDragAfter() inserts the dragged row before it.
    end_x = target_box["x"] + target_box["width"] / 2
    end_y = target_box["y"] + target_box["height"] / 2 - 8

    page.mouse.move(start_x, start_y)
    page.mouse.down()
    # Many small steps: makeSortable reorders on each pointermove, and small
    # deltas keep us inside the list / trigger the auto-scroll math cleanly.
    steps = 24
    for i in range(1, steps + 1):
        page.mouse.move(
            start_x + (end_x - start_x) * i / steps,
            start_y + (end_y - start_y) * i / steps,
        )
    # A nudge slightly past the drop point, then settle, so the final insert sticks.
    page.mouse.move(end_x, end_y - 4)
    page.mouse.move(end_x, end_y)
    page.mouse.up()

    # The DOM is reordered synchronously inside pointermove; check it took.
    return _row_ids(page).index(drag_id) < _row_ids(page).index(target_id)


def _reorder_via_page_fetch(page: Page, order: list[str]) -> int:
    """Diagnostic only: issue the EXACT request admin.js would send — POST the
    given id order as JSON to the list's data-reorder-url. Used solely to attribute
    a drag failure (JS sortable vs. server route); it never substitutes for the
    real gesture and never makes the test pass on its own. Returns the HTTP status."""
    return page.evaluate(
        """async (order) => {
            const list = document.getElementById('admList');
            const resp = await fetch(list.dataset.reorderUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order }),
            });
            return resp.status;
        }""",
        order,
    )


def test_admin_list_drag_reorder_persists_to_admin_and_public(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Drag the last product above the first; the new order persists in the admin
    list and is reflected on the public shop grid (C before A in both)."""
    base_url, password = admin_live_server

    # Use a unique, ordered prefix so we can identify OUR three rows among any
    # seeded products and assert their relative order unambiguously.
    title_a = "Reorder A E2E"
    title_b = "Reorder B E2E"
    title_c = "Reorder C E2E"

    _login(page, base_url, password)
    for title in (title_a, title_b, title_c):
        _create_product_with_image(page, base_url, title=title)

    # Newly created products get an increasing `position`, so they sort A, B, C
    # at the END of the list. Capture their ids in that order.
    page.goto(f"{base_url}/admin/", wait_until="load")
    expect(page.get_by_role("heading", name="Tus productos")).to_be_visible()

    def _id_for(title: str) -> str:
        return page.locator(
            f'#admList .adm-row:has(.adm-row-title:text-is("{title}"))'
        ).first.get_attribute("data-id")

    id_a, id_b, id_c = _id_for(title_a), _id_for(title_b), _id_for(title_c)
    assert id_a and id_b and id_c, (id_a, id_b, id_c)

    titles_before = _row_titles(page)
    pos = {t: titles_before.index(t) for t in (title_a, title_b, title_c)}
    assert pos[title_a] < pos[title_b] < pos[title_c], titles_before

    # --- Drag C above A; the GENUINE pointer drag must fire the reorder POST. ---
    # This is the whole point of the test: makeSortable on #admList must turn a
    # real handle drag into a fetch to data-reorder-url. We therefore require the
    # synthesized drag to (1) reorder the DOM and (2) produce a real reorder POST.
    # No silent fetch-fallback: a fallback that hand-issues the request would pass
    # even if makeSortable were unwired, so it must NOT keep the test green. We
    # verified the synthesized pointer drag registers headless in this env; if a
    # future environment genuinely can't drive it, run the documented diagnostic
    # in `_reorder_via_page_fetch` separately, but a broken sortable must FAIL here.
    drag_error: Exception | None = None
    dom_changed = False
    try:
        with page.expect_response(
            lambda r: "reorder" in r.url and r.request.method == "POST" and r.status == 200,
            timeout=8_000,
        ):
            dom_changed = _drag_row_above(page, drag_id=id_c, target_id=id_a)
    except Exception as exc:  # noqa: BLE001 — surface, don't mask, a non-firing drag
        drag_error = exc

    # Diagnostic only: if the drag never fired the POST, confirm the server route
    # itself still works (so a failure here points at the JS sortable, not the
    # backend) — but this branch must end in a FAIL, never a pass.
    if drag_error is not None or not dom_changed:
        status = _reorder_via_page_fetch(page, [id_c] + [i for i in _row_ids(page) if i != id_c])
        raise AssertionError(
            "Genuine handle drag did not fire the reorder POST "
            f"(dom_changed={dom_changed}, drag_error={drag_error!r}). "
            f"The reorder route itself responded {status} to a direct fetch, so the "
            "regression is in admin.js makeSortable/#admList wiring, not the server."
        )

    # (a) Reload the admin list — C must now come before A (and before B).
    page.goto(f"{base_url}/admin/", wait_until="load")
    after = _row_titles(page)
    assert after.index(title_c) < after.index(title_a), after
    assert after.index(title_c) < after.index(title_b), after

    # (b) Reload the public home — the shop grid order reflects the new order:
    # C's card appears before A's card.
    page.goto(f"{base_url}/", wait_until="load")
    expect(page.locator(".shop-grid")).to_be_visible()
    grid_names = [
        (n or "").strip()
        for n in page.locator(".shop-grid .product .product-name").all_inner_texts()
    ]
    assert title_c in grid_names and title_a in grid_names, grid_names
    assert grid_names.index(title_c) < grid_names.index(title_a), grid_names
