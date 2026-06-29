"""End-to-end VIDEO upload through the browser (Playwright sync API).

Dimension: the heaviest end-to-end path the admin supports — uploading a real
video through the JS-enhanced product form (app/static/js/admin.js). The submit
builds a FormData, POSTs it via XMLHttpRequest, the server transcodes the clip
with ffmpeg into a compressed MP4 + a poster JPEG, and only then does the
browser navigate back to /admin/. We then confirm the public site renders the
result correctly: a real <video> on the detail page (mp4 source + poster) and a
poster <img> plus a "Ver video" badge on the home grid.

The existing test_public_routes.py exercises the same video pipeline through the
test client (synchronous multipart POST); this file is the *browser* path —
verifying admin.js's video tile (▶ badge), its "Optimizando fotos y videos…"
progress text, and the XHR-then-navigate handshake actually work end to end.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e

# Whole module is meaningless without ffmpeg: there's no transcode to drive.
if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not available; cannot transcode an uploaded video", allow_module_level=True)

# Transcoding a clip server-side is far slower than writing JPEG variants, so the
# navigation-back wait gets a generous budget.
DEFAULT_TIMEOUT_MS = 30_000
TRANSCODE_TIMEOUT_MS = 60_000


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


@pytest.fixture(scope="module")
def sample_mp4() -> Iterator[str]:
    """A tiny real vertical MP4 (testsrc, 1s, yuv420p) in a tempfile dir.

    Generated with ffmpeg lavfi — never written into the repo. Same recipe as
    tests/test_public_routes.py::_make_test_video so it survives transcode.
    """
    tmp = tempfile.mkdtemp(prefix="gluck-e2e-video-")
    path = os.path.join(tmp, "clip.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x568:rate=15",
            "-pix_fmt", "yuv420p", path,
        ],
        check=True,
    )
    try:
        yield path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _login(page: Page, base_url: str, password: str) -> None:
    """Log into the admin and land on the products list."""
    page.goto(f"{base_url}/admin/", wait_until="load")
    expect(page).to_have_url(re.compile(r"/admin/login"))
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/admin/?$"))
    expect(page.get_by_role("heading", name="Tus productos")).to_be_visible()


def test_admin_video_upload_full_flow(
    admin_live_server: tuple[str, str], page: Page, sample_mp4: str
) -> None:
    """Upload a video through the JS form and verify the whole pipeline:

    - admin.js renders a video tile with the ▶ badge (distinct from image tiles).
    - On submit the overlay shows the transcode progress text.
    - The browser navigates back to /admin/ once ffmpeg finishes.
    - The public detail page renders a <video> with an mp4 <source> + poster, both
      served from /media/.
    - The home grid shows the product as a poster <img> with a "Ver video" badge.
    """
    base_url, password = admin_live_server
    title = "Cartera Video E2E"

    _login(page, base_url, password)

    page.get_by_role("link", name="+ Agregar producto").click()
    page.wait_for_url(re.compile(r"/admin/products/new$"))
    page.fill("#title", title)

    # Drive the visually-hidden input; admin.js mirrors the file into its map and
    # builds a tile. A video gets the .is-video thumb with a ▶ glyph (an <img>
    # thumbnail is only created for images), so this proves video detection.
    page.set_input_files("#fileInput", sample_mp4)
    video_thumb = page.locator("#tiles .tile .tile-thumb.is-video")
    expect(video_thumb).to_be_visible()
    expect(video_thumb).to_have_text("▶")
    # No <img> preview is generated for a video tile.
    expect(page.locator("#tiles .tile .tile-thumb img")).to_have_count(0)

    # Submit kicks off the XHR upload; on the upload 'load' event admin.js sets
    # the overlay text to the transcode message before the response arrives.
    page.get_by_role("button", name="Guardar").click()
    expect(page.locator("#admText")).to_have_text(
        "Optimizando fotos y videos…", timeout=TRANSCODE_TIMEOUT_MS
    )

    # ...then navigates back to the list once the server finishes transcoding.
    page.wait_for_url(re.compile(r"/admin/?$"), timeout=TRANSCODE_TIMEOUT_MS)
    expect(page.get_by_role("heading", name="Tus productos")).to_be_visible()
    expect(page.get_by_text(title, exact=True)).to_be_visible()

    # --- Public home grid: video cover renders as a poster <img> + badge ---
    page.goto(f"{base_url}/", wait_until="load")
    card = page.locator(".shop-grid .product").filter(has_text=title)
    expect(card).to_be_visible()
    # The grid shows the poster image (not a <video>) for a video cover.
    expect(card.locator(".product-media video")).to_have_count(0)
    poster_img = card.locator(".product-media img").first
    expect(poster_img).to_be_visible()
    poster_src = poster_img.get_attribute("src") or ""
    assert re.search(r"/media/.+/poster\.jpg$", poster_src), poster_src
    # The poster bytes actually loaded (real image served by ffmpeg's poster).
    assert poster_img.evaluate("img => img.naturalWidth") > 0, "grid poster failed to load"
    # The card advertises the video with the "Ver video" badge.
    expect(card.locator(".product-badge")).to_contain_text("Ver video")

    # --- Public detail page: a real <video> with mp4 source + poster ---
    href = card.get_attribute("href")
    assert href and re.search(r"/producto/\d+", href), href
    card.click()
    page.wait_for_url(re.compile(r"/producto/\d+"))
    expect(page.get_by_role("heading", name=title)).to_be_visible()

    video = page.locator(".pdp-gallery video.pdp-media").first
    expect(video).to_be_visible()

    # poster attribute points at the generated /media/.../poster.jpg.
    poster_attr = video.get_attribute("poster") or ""
    assert re.search(r"/media/.+/poster\.jpg$", poster_attr), poster_attr

    # The <source> points at the transcoded /media/.../video.mp4 (type video/mp4).
    source = video.locator("source")
    expect(source).to_have_attribute("type", "video/mp4")
    video_src = source.get_attribute("src") or ""
    assert re.search(r"/media/.+/video\.mp4$", video_src), video_src

    # The transcoded MP4 is actually served (not a 404): fetch it from the page
    # and assert a 2xx with a video content-type and non-empty body.
    status, ctype, length = page.evaluate(
        """async (url) => {
            const r = await fetch(url);
            const buf = await r.arrayBuffer();
            return [r.status, r.headers.get('content-type') || '', buf.byteLength];
        }""",
        video_src,
    )
    assert status == 200, f"video.mp4 not served: HTTP {status}"
    assert "video" in ctype.lower(), f"unexpected content-type for video: {ctype!r}"
    assert length > 0, "served video.mp4 was empty"
