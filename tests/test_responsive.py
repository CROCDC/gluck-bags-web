"""Responsive / horizontal-overflow guard (Playwright + the isolated E2E server).

The site sets ``body { overflow-x: hidden }`` (see static/css/styles.css), which
hides a naive ``document.scrollWidth > innerWidth`` overflow from the user but does
NOT fix the underlying layout: a container that pokes past the right edge is simply
clipped. So instead of trusting scrollWidth we inspect the actual layout boxes:
for the page's key containers we assert
``getBoundingClientRect().right <= viewport width + 1`` (the +1 absorbs sub-pixel
rounding). If a real container spills past the viewport at any tested width, that's
a responsive bug regardless of the overflow-x clip.

We sweep the common phone/tablet/desktop widths (375, 414, 768, 1024, 1280) across
the public home, a product detail page, the admin login, the admin product list and
the admin "new product" form, and drop a screenshot of every page+width under
``tests/screenshots/`` for visual inspection.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

# Single source of truth (shared with the overflow guard): viewport matrix +
# the structural containers we probe per page.
from pages import (
    ADMIN_FORM_CONTAINERS,
    ADMIN_LIST_CONTAINERS,
    ADMIN_LOGIN_CONTAINERS,
    HOME_CONTAINERS,
    OVERFLOW_TOLERANCE,
    PDP_CONTAINERS,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTHS,
)

SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"


def _ensure_screenshots_dir() -> Path:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS_DIR


# --- overflow probe ----------------------------------------------------------

# Runs in the page. For each CSS selector that matches at least one visible element,
# it returns the worst (largest) right-edge and the element's own outerHTML head, so
# a failure message can point at the offending container. We measure the *layout box*
# (getBoundingClientRect), which is unaffected by body overflow-x:hidden.
_PROBE_JS = """
(selectors) => {
  const vw = window.innerWidth;
  const results = [];
  for (const sel of selectors) {
    const nodes = Array.from(document.querySelectorAll(sel));
    for (const el of nodes) {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const rect = el.getBoundingClientRect();
      // Skip zero-area / collapsed elements (e.g. a hidden mobile menu): they
      // can report stale boxes and aren't part of the visible layout.
      if (rect.width === 0 && rect.height === 0) continue;
      results.push({
        selector: sel,
        right: rect.right,
        left: rect.left,
        width: rect.width,
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute('class') || ''),
      });
    }
  }
  return { vw, results };
}
"""

def _assert_no_overflow(page: Page, selectors: list[str], label: str) -> None:
    """Fail if any matched container's right edge exceeds the viewport width."""
    probe: dict[str, Any] = page.evaluate(_PROBE_JS, selectors)
    vw: float = probe["vw"]
    offenders = [
        r
        for r in probe["results"]
        if r["right"] > vw + OVERFLOW_TOLERANCE
    ]
    if offenders:
        lines = [
            f"  {o['tag']}.{o['cls'][:60]!r} (selector {o['selector']!r}): "
            f"right={o['right']:.1f} > vw={vw:.0f} (overflow {o['right'] - vw:.1f}px)"
            for o in offenders
        ]
        detail = "\n".join(lines)
        pytest.fail(f"{label}: {len(offenders)} container(s) overflow the viewport:\n{detail}")


def _open(browser: Browser, base_url: str, path: str, width: int) -> Page:
    """Open `path` in a fresh context sized to `width` and wait for layout."""
    context = browser.new_context(
        viewport={"width": width, "height": VIEWPORT_HEIGHT},
        device_scale_factor=1,
        # Emulate prefers-reduced-motion so the site's own CSS reveals every
        # `.reveal` section immediately (no opacity:0) and freezes the hero/marquee
        # animations. Without this, a full-page screenshot (which never scrolls)
        # captures the below-the-fold sections blank, and the visual baseline lies.
        reduced_motion="reduce",
    )
    page = context.new_page()
    page.goto(base_url.rstrip("/") + path, wait_until="load")
    # Let lazy images settle so boxes reach their final size.
    page.wait_for_timeout(300)
    return page


def _screenshot(page: Page, name: str) -> None:
    out = _ensure_screenshots_dir() / name
    page.screenshot(path=str(out), full_page=True)


def _discover_product_path(browser: Browser, base_url: str) -> str:
    """Parse the home HTML for the first /producto/<id> link and return its path."""
    context = browser.new_context(viewport={"width": 1280, "height": VIEWPORT_HEIGHT})
    page = context.new_page()
    page.goto(base_url.rstrip("/") + "/", wait_until="load")
    href = page.evaluate(
        "() => { const a = document.querySelector('a[href*=\"/producto/\"]');"
        " return a ? a.getAttribute('href') : null; }"
    )
    page.context.close()
    if not href:
        # Fallback: parse the raw HTML in case the link is rendered without JS hooks.
        context2 = browser.new_context()
        page2 = context2.new_page()
        page2.goto(base_url.rstrip("/") + "/", wait_until="load")
        html = page2.content()
        page2.context.close()
        match = re.search(r"/producto/\d+", html)
        href = match.group(0) if match else None
    assert href, "No /producto/<id> link found on the home page (seeded products expected)."
    # Normalise to a path (strip any origin/fragment).
    href = re.sub(r"^https?://[^/]+", "", href)
    return href.split("#")[0]


def _login(browser: Browser, base_url: str, password: str, width: int) -> Page:
    """Submit the admin login form and return the resulting (logged-in) page."""
    page = _open(browser, base_url, "/admin/login", width)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")
    page.wait_for_timeout(200)
    return page


# --- public pages ------------------------------------------------------------


@pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
def test_home_no_horizontal_overflow(admin_live_server, browser: Browser, width: int) -> None:
    base_url, _password = admin_live_server
    page = _open(browser, base_url, "/", width)
    try:
        _screenshot(page, f"home-{width}.png")
        _assert_no_overflow(page, HOME_CONTAINERS, f"home @ {width}px")
    finally:
        page.context.close()


@pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
def test_product_detail_no_horizontal_overflow(
    admin_live_server, browser: Browser, width: int
) -> None:
    base_url, _password = admin_live_server
    product_path = _discover_product_path(browser, base_url)
    page = _open(browser, base_url, product_path, width)
    try:
        slug = product_path.strip("/").replace("/", "-")
        _screenshot(page, f"{slug}-{width}.png")
        _assert_no_overflow(page, PDP_CONTAINERS, f"product detail {product_path} @ {width}px")
    finally:
        page.context.close()


# --- admin pages -------------------------------------------------------------


@pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
def test_admin_login_no_horizontal_overflow(
    admin_live_server, browser: Browser, width: int
) -> None:
    base_url, _password = admin_live_server
    page = _open(browser, base_url, "/admin/login", width)
    try:
        _screenshot(page, f"admin-login-{width}.png")
        _assert_no_overflow(page, ADMIN_LOGIN_CONTAINERS, f"admin login @ {width}px")
    finally:
        page.context.close()


@pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
def test_admin_list_no_horizontal_overflow(
    admin_live_server, browser: Browser, width: int
) -> None:
    base_url, password = admin_live_server
    page = _login(browser, base_url, password, width)
    try:
        # After login we should be on the product list (seeded => non-empty).
        assert "/admin" in page.url
        _screenshot(page, f"admin-list-{width}.png")
        _assert_no_overflow(page, ADMIN_LIST_CONTAINERS, f"admin list @ {width}px")
    finally:
        page.context.close()


@pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
def test_admin_new_product_no_horizontal_overflow(
    admin_live_server, browser: Browser, width: int
) -> None:
    base_url, password = admin_live_server
    page = _login(browser, base_url, password, width)
    try:
        page.goto(base_url.rstrip("/") + "/admin/products/new", wait_until="load")
        page.wait_for_timeout(200)
        _screenshot(page, f"admin-new-{width}.png")
        _assert_no_overflow(page, ADMIN_FORM_CONTAINERS, f"admin new product @ {width}px")
    finally:
        page.context.close()


def test_screenshots_dir_is_created(admin_live_server, browser: Browser) -> None:
    """Sanity: the screenshots directory exists and ends up populated."""
    base_url, _password = admin_live_server
    page = _open(browser, base_url, "/", 375)
    try:
        _screenshot(page, "home-375.png")
    finally:
        page.context.close()
    assert SCREENSHOTS_DIR.is_dir()
    assert any(SCREENSHOTS_DIR.iterdir()), "no screenshots were written"
    assert os.path.getsize(SCREENSHOTS_DIR / "home-375.png") > 0
