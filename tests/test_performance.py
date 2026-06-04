"""Site performance tests.

They open the landing page in headless Chromium (Playwright) and measure real
browser metrics: Core Web Vitals (LCP, CLS), Navigation Timing, page weight and
resource loading behaviour. Each test compares against a *performance budget*; if
the site gets slower, heavier or breaks a resource, the test fails and we catch it
before deploying.

Run only these tests:
    pytest tests/test_performance.py -v

The thresholds are measured locally without throttling. Adjust them if you want to
be more/less strict. Google Web Vitals reference ("good"): LCP < 2500ms, CLS < 0.1.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

# --- Performance budgets -----------------------------------------------------
# With self-hosted fonts the LCP (hero text) drops to ~400-900ms locally; we leave
# headroom to vary across machines/CI without becoming flaky.

LCP_BUDGET_MS = 1500          # Largest Contentful Paint
CLS_BUDGET = 0.1             # Cumulative Layout Shift
TTFB_BUDGET_MS = 800          # Time To First Byte
DOM_CONTENT_LOADED_BUDGET_MS = 2000
LOAD_BUDGET_MS = 4000
PAGE_WEIGHT_BUDGET_KB = 1500  # transferred weight of the first load (without videos)


@pytest.fixture(scope="session")
def page(browser: Browser, live_server: str) -> Iterator[Page]:
    """Chromium page already navigated to the site, with resources warm in cache.

    We do a first "warm-up" visit and then a reload to measure, so the measurement
    reflects the performance of the *site* without the network variance of the
    first download. (Cold start is covered separately below.)
    """
    page = browser.new_page()
    page.goto(live_server, wait_until="load")  # warm-up
    page.goto(live_server, wait_until="load")  # measured load
    page.wait_for_timeout(500)
    yield page
    page.close()


def _navigation_timing(page: Page) -> dict[str, float]:
    """Reads the Navigation Timing metrics (in ms, relative to navigationStart)."""
    return page.evaluate(
        """() => {
            const nav = performance.getEntriesByType('navigation')[0];
            return {
                ttfb: nav.responseStart,
                domContentLoaded: nav.domContentLoadedEventEnd,
                load: nav.loadEventEnd,
                transferSizeKb: performance
                    .getEntriesByType('resource')
                    .reduce((acc, r) => acc + (r.transferSize || 0), nav.transferSize || 0) / 1024,
            };
        }"""
    )


def _largest_contentful_paint(page: Page) -> float:
    """Returns the LCP in ms observed by PerformanceObserver."""
    return page.evaluate(
        """() => new Promise((resolve) => {
            let lcp = 0;
            new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    lcp = entry.startTime;
                }
            }).observe({ type: 'largest-contentful-paint', buffered: true });
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(lcp)));
        })"""
    )


def _cumulative_layout_shift(page: Page) -> float:
    """Returns the cumulative CLS observed by PerformanceObserver."""
    return page.evaluate(
        """() => new Promise((resolve) => {
            let cls = 0;
            new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (!entry.hadRecentInput) cls += entry.value;
                }
            }).observe({ type: 'layout-shift', buffered: true });
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(cls)));
        })"""
    )


# --- Metric tests (warm load) ------------------------------------------------


def test_time_to_first_byte(page: Page) -> None:
    ttfb = _navigation_timing(page)["ttfb"]
    assert ttfb < TTFB_BUDGET_MS, f"TTFB {ttfb:.0f}ms exceeds the budget of {TTFB_BUDGET_MS}ms"


def test_dom_content_loaded(page: Page) -> None:
    dcl = _navigation_timing(page)["domContentLoaded"]
    assert dcl < DOM_CONTENT_LOADED_BUDGET_MS, (
        f"DOMContentLoaded {dcl:.0f}ms exceeds the budget of {DOM_CONTENT_LOADED_BUDGET_MS}ms"
    )


def test_full_load(page: Page) -> None:
    load = _navigation_timing(page)["load"]
    assert load < LOAD_BUDGET_MS, f"load {load:.0f}ms exceeds the budget of {LOAD_BUDGET_MS}ms"


def test_largest_contentful_paint(page: Page) -> None:
    lcp = _largest_contentful_paint(page)
    assert lcp > 0, "Could not measure the LCP"
    assert lcp < LCP_BUDGET_MS, f"LCP {lcp:.0f}ms exceeds the budget of {LCP_BUDGET_MS}ms"


def test_cumulative_layout_shift(page: Page) -> None:
    cls = _cumulative_layout_shift(page)
    assert cls < CLS_BUDGET, f"CLS {cls:.3f} exceeds the budget of {CLS_BUDGET}"


def test_page_weight(page: Page) -> None:
    weight_kb = _navigation_timing(page)["transferSizeKb"]
    assert weight_kb < PAGE_WEIGHT_BUDGET_KB, (
        f"The page transfers {weight_kb:.0f}KB, exceeds the budget of {PAGE_WEIGHT_BUDGET_KB}KB"
    )


# --- Cold-start and resource-loading tests (own navigation) ------------------
# These start from an empty cache to reflect a brand-new visitor.


def _fresh_load(browser: Browser, url: str) -> tuple[Page, list]:
    """Navigates with a clean cache and returns (page, responses) to inspect."""
    context = browser.new_context()  # new context = no shared cache
    page = context.new_page()
    responses: list = []
    page.on("response", lambda r: responses.append(r))
    page.goto(url, wait_until="load")
    page.wait_for_timeout(300)
    return page, responses


def test_no_broken_requests(browser: Browser, live_server: str) -> None:
    """No page resource should return 4xx/5xx (catches broken paths)."""
    page, responses = _fresh_load(browser, live_server)
    broken = [(r.url, r.status) for r in responses if r.status >= 400]
    page.context.close()
    assert not broken, f"Broken resources: {broken}"


def test_cold_start_lcp(browser: Browser, live_server: str) -> None:
    """The cold LCP (first visitor, no cache) must stay within budget.

    This was the worst case before self-hosting the fonts: the render-blocking
    request to Google Fonts pushed the LCP to ~2.8s.
    """
    page, _ = _fresh_load(browser, live_server)
    lcp = _largest_contentful_paint(page)
    page.context.close()
    assert 0 < lcp < LCP_BUDGET_MS, f"Cold LCP {lcp:.0f}ms exceeds the budget of {LCP_BUDGET_MS}ms"


def test_heavy_videos_are_deferred(browser: Browser, live_server: str) -> None:
    """Background videos (several MB) must not be downloaded on the initial load.

    They load only when scrolling to their section (lazy), so they don't compete
    with the critical resources nor cause decoding jank during scroll.
    """
    page, responses = _fresh_load(browser, live_server)
    video_reqs = [r.url for r in responses if r.url.endswith((".webm", ".mp4"))]
    page.context.close()
    assert not video_reqs, f"Videos were downloaded on the initial load: {video_reqs}"


def test_no_render_blocking_third_party_fonts(browser: Browser, live_server: str) -> None:
    """Fonts must be served from the same origin, not from Google Fonts."""
    page, responses = _fresh_load(browser, live_server)
    google = [r.url for r in responses if "fonts.googleapis.com" in r.url or "fonts.gstatic.com" in r.url]
    page.context.close()
    assert not google, f"There are requests to Google Fonts (they should be self-hosted): {google}"
