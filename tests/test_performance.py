"""Site performance tests (hardened, statistical).

They open the site in headless Chromium (Playwright) and measure real browser
metrics: Core Web Vitals (LCP, CLS, INP-proxy), Navigation & Paint Timing, main
thread blocking (TBT / long tasks), page weight broken down by resource type,
request count, DOM size, JS heap, console errors, image hygiene and resource
loading behaviour. Each test compares against a *performance budget*; if the site
gets slower, heavier or breaks a resource, the test fails and we catch it before
deploying.

Unlike a naive perf test, the warm metrics are the **median of several loads**, so
a single noisy measurement can't make the suite flaky (or green by luck).

Run only these tests:
    pytest tests/test_performance.py -v

Every test runs every time — nothing is skipped. The compression test needs the
app (or proxy) to gzip/br responses (e.g. Flask-Compress); the caching test needs
a long Cache-Control. Both pass locally once the app is configured that way.

Operational knobs (env vars, optional — none of them skip a test):
    PERF_SAMPLES=5             number of warm loads to median over
    PERF_TARGET_URL=https://…  test a deployed URL instead of the local server
                               (set in conftest's live_server fixture)

The warm/cold metric tests run unthrottled; the device matrix (DEVICE_PROFILES)
emulates real devices Lighthouse-style (viewport + DPR + CPU + network throttle)
with per-device budgets. Calibrate to your project. Google Web Vitals reference
("good"): LCP < 2500ms, CLS < 0.1, INP < 200ms.
"""

from __future__ import annotations

import os
import re
import statistics
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Browser, Page

# Slow: every test here spins up a real server and measures it. Deselect with
# `pytest -m "not perf"`.
pytestmark = pytest.mark.perf

# --- Performance budgets -----------------------------------------------------
# Calibrate these to your site after the first run (the skill suggests values).

LCP_BUDGET_MS = 1500           # Largest Contentful Paint (warm)
FCP_BUDGET_MS = 1500           # First Contentful Paint
CLS_BUDGET = 0.1               # Cumulative Layout Shift
TTFB_BUDGET_MS = 800           # Time To First Byte
DOM_CONTENT_LOADED_BUDGET_MS = 2000
LOAD_BUDGET_MS = 4000
TBT_BUDGET_MS = 300            # Total Blocking Time (sum of long-task overage)
MAX_LONG_TASK_MS = 250         # No single main-thread task may block this long

# Weight & request budgets are measured on a COLD load (empty cache).
# Calibrated to gluck-bags (cold: ~1234KB total, image ~1070, css ~92, font ~64,
# 23 requests, 294 DOM nodes) with ~30% headroom for CI/machine variance.
PAGE_WEIGHT_BUDGET_KB = 1500   # total transferred weight of the first load (no video)
JS_WEIGHT_BUDGET_KB = 100
CSS_WEIGHT_BUDGET_KB = 130
IMAGE_WEIGHT_BUDGET_KB = 1300
FONT_WEIGHT_BUDGET_KB = 100

REQUEST_COUNT_BUDGET = 32      # number of requests on the cold load
DOM_NODES_BUDGET = 450         # excessive DOM hurts style/layout cost
JS_HEAP_BUDGET_MB = 20
THIRD_PARTY_ORIGINS_BUDGET = 3 # distinct cross-origin hosts on a cold load
RENDER_BLOCKING_CSS_BUDGET = 2 # render-blocking stylesheets allowed in <head>

# Per-device budgets (LCP/TBT/CLS) live in DEVICE_PROFILES near the device matrix.


# --- Metric collection -------------------------------------------------------
# One JS pass gathers every observer- and timing-based metric for a single load.

_COLLECT_JS = r"""
() => new Promise((resolve) => {
    let lcp = 0, cls = 0;
    const longTasks = [];
    const obs = (type, cb) => { try {
        new PerformanceObserver((l) => { for (const e of l.getEntries()) cb(e); })
            .observe({ type, buffered: true });
    } catch (e) {} };
    obs('largest-contentful-paint', (e) => { lcp = e.startTime; });
    obs('layout-shift', (e) => { if (!e.hadRecentInput) cls += e.value; });
    obs('longtask', (e) => { longTasks.push(e); });

    // Let the observers flush their buffered entries before we read.
    requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(() => {
        const nav = performance.getEntriesByType('navigation')[0] || {};
        const fcpEntry = performance.getEntriesByType('paint')
            .find((p) => p.name === 'first-contentful-paint');
        const fcp = fcpEntry ? fcpEntry.startTime : 0;

        const typeOf = (r) => {
            const it = r.initiatorType, u = r.name;
            if (it === 'script' || /\.m?js(\?|$)/.test(u)) return 'script';
            if (it === 'css' || /\.css(\?|$)/.test(u) ||
                (it === 'link' && /\.css(\?|$)/.test(u))) return 'css';
            if (it === 'img' || /\.(png|jpe?g|gif|webp|avif|svg|ico)(\?|$)/.test(u)) return 'image';
            if (/\.(woff2?|ttf|otf|eot)(\?|$)/.test(u)) return 'font';
            if (it === 'media' || /\.(mp4|webm|ogg|mp3|wav|m4a)(\?|$)/.test(u)) return 'media';
            if (it === 'xmlhttprequest' || it === 'fetch') return 'xhr';
            return 'other';
        };
        const res = performance.getEntriesByType('resource');
        const byType = {};
        let totalKb = (nav.transferSize || 0) / 1024;
        for (const r of res) {
            const kb = (r.transferSize || 0) / 1024;
            const t = typeOf(r);
            byType[t] = (byType[t] || 0) + kb;
            totalKb += kb;
        }

        let tbt = 0, maxLongTask = 0;
        for (const t of longTasks) {
            if (t.startTime >= fcp) tbt += Math.max(0, t.duration - 50);
            maxLongTask = Math.max(maxLongTask, t.duration);
        }

        resolve({
            ttfb: nav.responseStart || 0,
            fcp,
            lcp,
            cls,
            domContentLoaded: nav.domContentLoadedEventEnd || 0,
            load: nav.loadEventEnd || 0,
            tbt,
            longTasks: longTasks.length,
            maxLongTask,
            requests: res.length,
            totalKb,
            byType,
            domNodes: document.getElementsByTagName('*').length,
            jsHeapMb: (performance.memory ? performance.memory.usedJSHeapSize : 0) / (1024 * 1024),
        });
    }, 0)));
})
"""


def _samples_count() -> int:
    try:
        return max(1, int(os.environ.get("PERF_SAMPLES", "5")))
    except ValueError:
        return 5


@pytest.fixture(scope="session")
def warm_samples(browser: Browser, live_server: str) -> list[dict]:
    """Median-ready list of full metric dicts from N warm loads.

    A first visit warms the cache, then we measure the *site* (not the network
    variance of the first download) across several reloads. Cold start is covered
    separately below.
    """
    page = browser.new_page()
    page.goto(live_server, wait_until="load")  # warm-up
    samples: list[dict] = []
    for _ in range(_samples_count()):
        page.goto(live_server, wait_until="load")
        page.wait_for_timeout(400)
        samples.append(page.evaluate(_COLLECT_JS))
    page.close()
    return samples


@pytest.fixture(scope="session")
def cold_metrics(browser: Browser, live_server: str) -> dict:
    """Full metrics from a single cold load (empty cache).

    Page weight, per-type weight and request count are measured here, *not* on the
    warm load: on a reload the resources come from cache and report transferSize 0,
    which would make weight budgets meaningless. This reflects what a brand-new
    visitor actually downloads.
    """
    context = browser.new_context()  # new context = empty cache
    page = context.new_page()
    page.goto(live_server, wait_until="load")
    page.wait_for_timeout(400)
    metrics = page.evaluate(_COLLECT_JS)
    context.close()
    return metrics


def _median(samples: list[dict], key: str) -> float:
    return statistics.median(float(s[key]) for s in samples)


# --- Warm-load metric tests (median of N) ------------------------------------


def test_time_to_first_byte(warm_samples: list[dict]) -> None:
    ttfb = _median(warm_samples, "ttfb")
    assert ttfb < TTFB_BUDGET_MS, f"TTFB {ttfb:.0f}ms exceeds the budget of {TTFB_BUDGET_MS}ms"


def test_first_contentful_paint(warm_samples: list[dict]) -> None:
    fcp = _median(warm_samples, "fcp")
    assert fcp > 0, "Could not measure the FCP"
    assert fcp < FCP_BUDGET_MS, f"FCP {fcp:.0f}ms exceeds the budget of {FCP_BUDGET_MS}ms"


def test_dom_content_loaded(warm_samples: list[dict]) -> None:
    dcl = _median(warm_samples, "domContentLoaded")
    assert dcl < DOM_CONTENT_LOADED_BUDGET_MS, (
        f"DOMContentLoaded {dcl:.0f}ms exceeds the budget of {DOM_CONTENT_LOADED_BUDGET_MS}ms"
    )


def test_full_load(warm_samples: list[dict]) -> None:
    load = _median(warm_samples, "load")
    assert load < LOAD_BUDGET_MS, f"load {load:.0f}ms exceeds the budget of {LOAD_BUDGET_MS}ms"


def test_largest_contentful_paint(warm_samples: list[dict]) -> None:
    lcp = _median(warm_samples, "lcp")
    assert lcp > 0, "Could not measure the LCP"
    assert lcp < LCP_BUDGET_MS, f"LCP {lcp:.0f}ms exceeds the budget of {LCP_BUDGET_MS}ms"


def test_cumulative_layout_shift(warm_samples: list[dict]) -> None:
    cls = _median(warm_samples, "cls")
    assert cls < CLS_BUDGET, f"CLS {cls:.3f} exceeds the budget of {CLS_BUDGET}"


def test_total_blocking_time(warm_samples: list[dict]) -> None:
    tbt = _median(warm_samples, "tbt")
    assert tbt < TBT_BUDGET_MS, f"TBT {tbt:.0f}ms exceeds the budget of {TBT_BUDGET_MS}ms"


def test_no_excessive_long_tasks(warm_samples: list[dict]) -> None:
    worst = _median(warm_samples, "maxLongTask")
    assert worst < MAX_LONG_TASK_MS, (
        f"Longest main-thread task {worst:.0f}ms exceeds {MAX_LONG_TASK_MS}ms (blocks interactivity)"
    )


# Page weight, per-type weight and request count are measured COLD (empty cache),
# because warm reloads serve from cache and report transferSize 0.


def test_page_weight(cold_metrics: dict) -> None:
    weight = cold_metrics["totalKb"]
    assert weight < PAGE_WEIGHT_BUDGET_KB, (
        f"The page transfers {weight:.0f}KB, exceeds the budget of {PAGE_WEIGHT_BUDGET_KB}KB"
    )


def test_javascript_weight(cold_metrics: dict) -> None:
    js = cold_metrics["byType"].get("script", 0.0)
    assert js < JS_WEIGHT_BUDGET_KB, f"JS weighs {js:.0f}KB, exceeds the budget of {JS_WEIGHT_BUDGET_KB}KB"


def test_css_weight(cold_metrics: dict) -> None:
    css = cold_metrics["byType"].get("css", 0.0)
    assert css < CSS_WEIGHT_BUDGET_KB, f"CSS weighs {css:.0f}KB, exceeds the budget of {CSS_WEIGHT_BUDGET_KB}KB"


def test_image_weight(cold_metrics: dict) -> None:
    img = cold_metrics["byType"].get("image", 0.0)
    assert img < IMAGE_WEIGHT_BUDGET_KB, (
        f"Images weigh {img:.0f}KB, exceeds the budget of {IMAGE_WEIGHT_BUDGET_KB}KB"
    )


def test_font_weight(cold_metrics: dict) -> None:
    font = cold_metrics["byType"].get("font", 0.0)
    assert font < FONT_WEIGHT_BUDGET_KB, (
        f"Fonts weigh {font:.0f}KB, exceeds the budget of {FONT_WEIGHT_BUDGET_KB}KB"
    )


def test_request_count(cold_metrics: dict) -> None:
    reqs = cold_metrics["requests"]
    assert reqs < REQUEST_COUNT_BUDGET, (
        f"The page makes {reqs:.0f} requests, exceeds the budget of {REQUEST_COUNT_BUDGET}"
    )


def test_dom_node_count(warm_samples: list[dict]) -> None:
    nodes = _median(warm_samples, "domNodes")
    assert nodes < DOM_NODES_BUDGET, (
        f"The DOM has {nodes:.0f} nodes, exceeds the budget of {DOM_NODES_BUDGET}"
    )


def test_js_heap_size(warm_samples: list[dict]) -> None:
    heap = _median(warm_samples, "jsHeapMb")
    assert heap < JS_HEAP_BUDGET_MB, (
        f"JS heap uses {heap:.1f}MB, exceeds the budget of {JS_HEAP_BUDGET_MB}MB"
    )


# --- Cold-start & resource-loading tests (own navigation) --------------------
# These start from an empty cache to reflect a brand-new visitor.


def _fresh_load(browser: Browser, url: str) -> tuple[Page, list, list[str]]:
    """Navigate with a clean cache. Returns (page, responses, console_errors)."""
    context = browser.new_context()  # new context = no shared cache
    page = context.new_page()
    responses: list = []
    console_errors: list[str] = []
    page.on("response", lambda r: responses.append(r))
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_timeout(400)
    return page, responses, console_errors


def test_no_broken_requests(browser: Browser, live_server: str) -> None:
    """No page resource should return 4xx/5xx (catches broken paths)."""
    page, responses, _ = _fresh_load(browser, live_server)
    broken = [(r.url, r.status) for r in responses if r.status >= 400]
    page.context.close()
    assert not broken, f"Broken resources: {broken}"


def test_cold_start_lcp(browser: Browser, live_server: str) -> None:
    """The cold LCP (first visitor, no cache) must stay within budget."""
    page, _, _ = _fresh_load(browser, live_server)
    lcp = page.evaluate(_COLLECT_JS)["lcp"]
    page.context.close()
    assert 0 < lcp < LCP_BUDGET_MS, f"Cold LCP {lcp:.0f}ms exceeds the budget of {LCP_BUDGET_MS}ms"


def test_no_console_errors(browser: Browser, live_server: str) -> None:
    """The page must load without console errors or uncaught exceptions."""
    page, _, console_errors = _fresh_load(browser, live_server)
    page.context.close()
    assert not console_errors, f"Console errors on load: {console_errors}"


def test_media_is_deferred_on_initial_load(browser: Browser, live_server: str) -> None:
    """Heavy media (videos/audio, several MB) must not download on the first load.

    They should load lazily when scrolled into view, so they don't compete with
    critical resources nor cause decode jank during scroll.
    """
    page, responses, _ = _fresh_load(browser, live_server)
    media = [r.url for r in responses if r.url.endswith((".webm", ".mp4", ".ogg", ".mov", ".m4v"))]
    page.context.close()
    assert not media, f"Media downloaded on the initial load: {media}"


def test_no_render_blocking_third_party_fonts(browser: Browser, live_server: str) -> None:
    """Fonts must be self-hosted, not fetched render-blocking from Google Fonts."""
    page, responses, _ = _fresh_load(browser, live_server)
    google = [r.url for r in responses
              if "fonts.googleapis.com" in r.url or "fonts.gstatic.com" in r.url]
    page.context.close()
    assert not google, f"Requests to Google Fonts (should be self-hosted): {google}"


def test_no_render_blocking_scripts_in_head(browser: Browser, live_server: str) -> None:
    """<head> scripts must be async/defer/module so they don't block first paint."""
    page, _, _ = _fresh_load(browser, live_server)
    blocking = page.evaluate(
        """() => Array.from(document.head.querySelectorAll('script[src]'))
            .filter((s) => !s.async && !s.defer && s.type !== 'module')
            .map((s) => s.src)"""
    )
    page.context.close()
    assert not blocking, f"Render-blocking scripts in <head>: {blocking}"


def test_render_blocking_stylesheets_within_budget(browser: Browser, live_server: str) -> None:
    """Limit render-blocking CSS in <head>: each one delays first paint / LCP.

    A stylesheet is render-blocking when it applies to the current media and isn't
    deferred (the rel=preload + onload swap pattern, media=print, or disabled). One
    critical stylesheet is fine; many — or large ones — are a Lighthouse finding.
    Defer non-critical CSS or inline the critical part.
    """
    page, _, _ = _fresh_load(browser, live_server)
    blocking = page.evaluate(
        """() => Array.from(document.head.querySelectorAll('link[rel="stylesheet"]'))
            .filter((l) => {
                if (l.disabled) return false;
                const m = (l.media || 'all').toLowerCase();
                if (m === 'print') return false;
                return window.matchMedia(m).matches;
            })
            .map((l) => l.href)"""
    )
    page.context.close()
    assert len(blocking) <= RENDER_BLOCKING_CSS_BUDGET, (
        f"{len(blocking)} render-blocking stylesheets, exceeds {RENDER_BLOCKING_CSS_BUDGET}: {blocking}"
    )


def test_images_use_modern_formats(browser: Browser, live_server: str) -> None:
    """Raster images should be WebP/AVIF, not PNG/JPEG/GIF (smaller for same quality)."""
    page, _, _ = _fresh_load(browser, live_server)
    legacy = page.evaluate(
        """() => Array.from(document.images)
            .map((img) => img.currentSrc || img.src)
            .filter((u) => /\\.(png|jpe?g|gif|bmp|tiff?)(\\?|$)/i.test(u))"""
    )
    page.context.close()
    assert not legacy, f"Images in legacy formats (use WebP/AVIF): {legacy}"


def test_images_have_explicit_dimensions(browser: Browser, live_server: str) -> None:
    """Images need width/height (or CSS aspect-ratio) to reserve space (avoid CLS)."""
    page, _, _ = _fresh_load(browser, live_server)
    missing = page.evaluate(
        """() => Array.from(document.images).filter((img) => {
            const hasAttr = img.getAttribute('width') && img.getAttribute('height');
            const ar = getComputedStyle(img).aspectRatio;
            const hasAspect = ar && ar !== 'auto';
            return !hasAttr && !hasAspect;
        }).map((img) => img.currentSrc || img.src)"""
    )
    page.context.close()
    assert not missing, f"Images without explicit dimensions (cause CLS): {missing}"


def test_images_are_not_oversized(browser: Browser, live_server: str) -> None:
    """Images must not be served much larger than they're displayed (wasted bytes)."""
    page, _, _ = _fresh_load(browser, live_server)
    oversized = page.evaluate(
        """() => { const dpr = window.devicePixelRatio || 1;
            return Array.from(document.images).filter((img) =>
                img.naturalWidth && img.clientWidth &&
                img.naturalWidth > img.clientWidth * dpr * 2
            ).map((img) => ({
                src: img.currentSrc || img.src,
                natural: img.naturalWidth,
                displayed: Math.round(img.clientWidth * dpr),
            })); }"""
    )
    page.context.close()
    assert not oversized, f"Oversized images (served >2x displayed size): {oversized}"


def test_below_fold_images_are_lazy(browser: Browser, live_server: str) -> None:
    """Images below the fold should use loading=\"lazy\" so they don't block the load."""
    page, _, _ = _fresh_load(browser, live_server)
    eager = page.evaluate(
        """() => { const vh = window.innerHeight;
            return Array.from(document.images).filter((img) =>
                img.getBoundingClientRect().top > vh && img.loading !== 'lazy'
            ).map((img) => img.currentSrc || img.src); }"""
    )
    page.context.close()
    assert not eager, f"Below-the-fold images not lazy-loaded: {eager}"


def test_limited_third_party_origins(browser: Browser, live_server: str) -> None:
    """Few cross-origin hosts: each one is a DNS+TLS handshake and a privacy/perf risk."""
    page, responses, _ = _fresh_load(browser, live_server)
    base_host = urlparse(live_server).hostname
    origins = {urlparse(r.url).hostname for r in responses
               if urlparse(r.url).hostname and urlparse(r.url).hostname != base_host
               and urlparse(r.url).scheme in ("http", "https")}
    page.context.close()
    assert len(origins) <= THIRD_PARTY_ORIGINS_BUDGET, (
        f"{len(origins)} third-party origins, exceeds {THIRD_PARTY_ORIGINS_BUDGET}: {sorted(origins)}"
    )


# --- Header tests (compression & caching) ------------------------------------
# These need the served responses to carry the right headers. Compression must be
# done by the app (e.g. Flask-Compress) or the reverse proxy; caching by a long
# Cache-Control (+ cache-busting). They run always and only check our own origin.

_TEXT_CT = ("text/html", "text/css", "application/javascript", "text/javascript",
            "application/json", "image/svg+xml")


def _is_same_origin(url: str, base_url: str) -> bool:
    return urlparse(url).hostname == urlparse(base_url).hostname


def test_text_resources_are_compressed(browser: Browser, live_server: str) -> None:
    """Same-origin text resources must be compressed. Third parties are their own
    responsibility, so we only assert on our own origin."""
    page, responses, _ = _fresh_load(browser, live_server)
    uncompressed = []
    for r in responses:
        if not _is_same_origin(r.url, live_server):
            continue
        ct = (r.header_value("content-type") or "").split(";")[0].strip()
        cl = r.header_value("content-length")
        if ct in _TEXT_CT and cl and int(cl) > 1024:
            enc = (r.header_value("content-encoding") or "").lower()
            if not any(a in enc for a in ("gzip", "br", "zstd", "deflate")):
                uncompressed.append(r.url)
    page.context.close()
    assert not uncompressed, f"Text resources served without compression: {uncompressed}"


def test_static_assets_are_cacheable(browser: Browser, live_server: str) -> None:
    """Same-origin static assets must have Cache-Control max-age >= 1 day.
    Only checks our own origin (we can't fix third parties' cache headers)."""
    page, responses, _ = _fresh_load(browser, live_server)
    not_cacheable = []
    static_ext = (".js", ".css", ".woff", ".woff2", ".ttf", ".otf",
                  ".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg", ".ico")
    for r in responses:
        if not _is_same_origin(r.url, live_server):
            continue
        path = urlparse(r.url).path
        if path.endswith(static_ext):
            cc = (r.header_value("cache-control") or "").lower()
            m = re.search(r"max-age=(\d+)", cc)
            if "no-store" in cc or "no-cache" in cc or not m or int(m.group(1)) < 86400:
                not_cacheable.append((r.url, cc or "<none>"))
    page.context.close()
    assert not not_cacheable, f"Static assets not cacheable (>=1 day): {not_cacheable}"


# --- Device matrix (Lighthouse-style emulation) ------------------------------
# Each profile emulates a device CLASS: viewport + DPR + (mobile UA/touch) + CPU
# slowdown + network throttle, the way Lighthouse does. LCP/TBT/CLS are measured
# per device (median of N) against per-device budgets. The low-end mobile profile
# mirrors Lighthouse's default mobile preset (Moto-G-class, 4x CPU, ~Slow 4G).
#
# net = (download Mbps, upload Kbps, RTT ms) or None for no network throttling.

_UA = {
    "android": ("Mozilla/5.0 (Linux; Android 13; moto g power) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
    "iphone": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    "ipad": ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
}

DEVICE_PROFILES = [
    {   # Lighthouse default mobile preset — the strict one.
        "id": "moto-g-lowend", "viewport": {"width": 412, "height": 823}, "dpr": 1.75,
        "mobile": True, "ua": _UA["android"], "cpu": 4, "net": (1.6, 750, 150),
        "lcp": 4000, "tbt": 600, "cls": 0.1,
    },
    {   # Mid-range phone on LTE.
        "id": "iphone-12", "viewport": {"width": 390, "height": 844}, "dpr": 3,
        "mobile": True, "ua": _UA["iphone"], "cpu": 3, "net": (9, 1500, 60),
        "lcp": 3000, "tbt": 450, "cls": 0.1,
    },
    {   # Tablet, lightly throttled, no network cap.
        "id": "ipad-mini", "viewport": {"width": 768, "height": 1024}, "dpr": 2,
        "mobile": True, "ua": _UA["ipad"], "cpu": 2, "net": None,
        "lcp": 2500, "tbt": 350, "cls": 0.1,
    },
    {   # Lighthouse desktop preset — fast, unthrottled.
        "id": "desktop-1080p", "viewport": {"width": 1350, "height": 940}, "dpr": 1,
        "mobile": False, "ua": None, "cpu": 1, "net": None,
        "lcp": 2000, "tbt": 250, "cls": 0.1,
    },
]

# How many loads to median per device (throttled loads are noisy). Override with
# PERF_DEVICE_SAMPLES; min 1.
try:
    _DEVICE_SAMPLES = max(1, int(os.environ.get("PERF_DEVICE_SAMPLES", "3")))
except ValueError:
    _DEVICE_SAMPLES = 3


@pytest.fixture(scope="session", params=DEVICE_PROFILES, ids=[p["id"] for p in DEVICE_PROFILES])
def device_samples(request, browser: Browser, live_server: str) -> tuple[dict, list[dict]]:
    """(profile, samples) for one emulated device: viewport + DPR + CPU + network
    throttle applied via CDP, median-ready over a few cold loads."""
    profile = request.param
    samples: list[dict] = []
    for _ in range(_DEVICE_SAMPLES):
        ctx: dict = {"viewport": profile["viewport"], "device_scale_factor": profile["dpr"]}
        if profile["mobile"]:
            ctx.update(is_mobile=True, has_touch=True)
        if profile["ua"]:
            ctx["user_agent"] = profile["ua"]
        context = browser.new_context(**ctx)
        page = context.new_page()
        client = context.new_cdp_session(page)
        if profile["net"]:
            down, up, rtt = profile["net"]
            client.send("Network.emulateNetworkConditions", {
                "offline": False, "latency": rtt,
                "downloadThroughput": int(down * 1024 * 1024 / 8),
                "uploadThroughput": int(up * 1024 / 8),
            })
        if profile["cpu"] > 1:
            client.send("Emulation.setCPUThrottlingRate", {"rate": profile["cpu"]})
        page.goto(live_server, wait_until="load")
        page.wait_for_timeout(500)
        samples.append(page.evaluate(_COLLECT_JS))
        context.close()
    return profile, samples


def test_device_largest_contentful_paint(device_samples: tuple[dict, list[dict]]) -> None:
    profile, samples = device_samples
    lcp = _median(samples, "lcp")
    assert 0 < lcp < profile["lcp"], (
        f"[{profile['id']}] LCP {lcp:.0f}ms exceeds the budget of {profile['lcp']}ms"
    )


def test_device_total_blocking_time(device_samples: tuple[dict, list[dict]]) -> None:
    profile, samples = device_samples
    tbt = _median(samples, "tbt")
    assert tbt < profile["tbt"], (
        f"[{profile['id']}] TBT {tbt:.0f}ms exceeds the budget of {profile['tbt']}ms"
    )


def test_device_cumulative_layout_shift(device_samples: tuple[dict, list[dict]]) -> None:
    profile, samples = device_samples
    cls = _median(samples, "cls")
    assert cls < profile["cls"], (
        f"[{profile['id']}] CLS {cls:.3f} exceeds the budget of {profile['cls']}"
    )
