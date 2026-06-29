"""Guard test: protects the validity of the responsive/overflow layer.

The reusable pattern (see the testing strategy): *a guard test that protects
another test layer from a silent-pass failure mode.* The canonical version of
this guard fails if any page sets ``overflow-x: hidden`` on <html>/<body>,
because that clamps ``scrollWidth`` and makes a scrollWidth-based overflow check
pass on a broken layout.

This site is a deliberate exception to the canonical version: it sets
``body { overflow-x: hidden }`` on purpose (see static/css/styles.css). So
``test_responsive.py`` does NOT trust ``scrollWidth`` — it measures each
container's ``getBoundingClientRect().right`` instead, which is immune to the
clip. That sidesteps the scrollWidth footgun, but introduces a DIFFERENT
silent-pass footgun: the box check only inspects selectors that actually match
elements. If a structural container's class is renamed (e.g. ``.shop-grid`` ->
``.products-grid``), the probe finds zero nodes for that selector, reports zero
offenders, and the responsive test passes green — while silently no longer
guarding that container.

This guard closes that hole: for every public page it asserts each declared
structural selector still resolves to at least one visible element. Until it
passes, the responsive overflow numbers don't necessarily cover what they claim.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from pages import PDP_CONTAINERS, PUBLIC_PAGE_CONTAINERS, VIEWPORT_HEIGHT

# Counts, per selector, how many *visible* elements match — the same visibility
# rules the overflow probe uses, so "resolves here" means "the overflow check
# would actually inspect it".
_RESOLVE_JS = """
(selectors) => {
  const out = {};
  for (const sel of selectors) {
    let visible = 0;
    for (const el of document.querySelectorAll(sel)) {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      visible += 1;
    }
    out[sel] = visible;
  }
  return out;
}
"""


def _discover_product_path(browser: Browser, base_url: str) -> str:
    """Return the path of the first /producto/<id> link on the home page."""
    context = browser.new_context(viewport={"width": 1280, "height": VIEWPORT_HEIGHT})
    page = context.new_page()
    try:
        page.goto(base_url.rstrip("/") + "/", wait_until="load")
        html = page.content()
    finally:
        context.close()
    match = re.search(r"/producto/\d+", html)
    assert match, "No /producto/<id> link found on the home page (seeded products expected)."
    return match.group(0)


def _assert_selectors_resolve(
    page: Page, base_url: str, path: str, selectors: list[str]
) -> None:
    page.goto(base_url.rstrip("/") + path, wait_until="load")
    page.wait_for_timeout(300)
    counts: dict[str, Any] = page.evaluate(_RESOLVE_JS, selectors)
    missing = [sel for sel in selectors if not counts.get(sel)]
    assert not missing, (
        f"{path}: structural selector(s) matched no visible element — the overflow "
        f"check silently stopped guarding them (renamed/removed?): {missing}"
    )


@pytest.mark.parametrize("path", sorted(PUBLIC_PAGE_CONTAINERS))
def test_public_page_overflow_selectors_resolve(
    admin_live_server: tuple[str, str], browser: Browser, path: str
) -> None:
    base_url, _password = admin_live_server
    context = browser.new_context(viewport={"width": 1280, "height": VIEWPORT_HEIGHT})
    page = context.new_page()
    try:
        _assert_selectors_resolve(page, base_url, path, PUBLIC_PAGE_CONTAINERS[path])
    finally:
        context.close()


def test_product_detail_overflow_selectors_resolve(
    admin_live_server: tuple[str, str], browser: Browser
) -> None:
    base_url, _password = admin_live_server
    product_path = _discover_product_path(browser, base_url)
    context = browser.new_context(viewport={"width": 1280, "height": VIEWPORT_HEIGHT})
    page = context.new_page()
    try:
        _assert_selectors_resolve(page, base_url, product_path, PDP_CONTAINERS)
    finally:
        context.close()
