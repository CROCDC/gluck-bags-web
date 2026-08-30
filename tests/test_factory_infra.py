"""Infrastructure behaviors wired up in app/factory.py that only matter in prod.

Dimension: factory_infra. These are the cross-cutting concerns the page renderers
and CRUD tests never touch but that silently degrade production if they regress:
gzip/brotli compression, the static cache-buster (with its deliberate font
exclusion that guards an LCP/preload bug), CSS inlining + url() rewriting, the
1-year static Cache-Control, secret-key persistence across restarts, the SQLite
WAL/busy_timeout pragmas, and the injected template globals (brand/instagram/year).

Each test asserts an OBSERVABLE outcome that would actually break if the wiring
were removed — not that the code merely runs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import Flask, url_for
from flask.testing import FlaskClient

# A real JPEG shipped with the app — used to create a product so the home HTML is
# large enough to cross flask-compress's min size threshold.

_SAMPLE_JPEG = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "static"
    / "img"
    / "productos"
    / "crossbody-rosa.jpg"
)


def _seed_one_product(auth_client: FlaskClient) -> None:
    """Create a single published product through the admin form so the public
    home renders a non-trivial document body."""
    import io

    data = {
        "title": "Bolso Infra",
        "is_published": "on",
        "price": "45000",
        "media": (io.BytesIO(_SAMPLE_JPEG.read_bytes()), "foto.jpg"),
        "order": '["new:0"]',
    }
    resp = auth_client.post(
        "/admin/products/new", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 302, resp.data


# --- 1) gzip / brotli compression ---------------------------------------------


def test_home_is_compressed_when_client_accepts_gzip(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    """flask-compress encodes the (large, CSS-inlined) home HTML when the client
    advertises gzip; without it the proxy-independent compression is gone."""
    # Ensure the body comfortably clears flask-compress's size threshold.
    _seed_one_product(auth_client)

    resp = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") in ("gzip", "br", "deflate"), dict(
        resp.headers
    )


# --- 2) static cache-buster (?v=mtime) with intentional font exclusion ---------


def test_static_url_is_cache_busted_but_fonts_are_not(app: Flask) -> None:
    """Versioned static URLs (?v=mtime) force re-fetch on change, but fonts are
    deliberately NOT versioned: a versioned preload URL would differ from the
    @font-face url(), wasting the preload and double-downloading the font (LCP)."""
    # A REQUEST context, not just an app context: `url_for` needs a URL adapter to
    # build a relative URL, and outside a request Flask only has one when SERVER_NAME
    # is configured — which this app deliberately does not set (the canonical origin
    # lives in SITE_URL). The `?v=` cache-buster is a url_defaults hook, so it runs
    # the same either way; this is how the templates call url_for in real life.
    with app.test_request_context("/"):
        js_url = url_for("static", filename="js/main.js")
        font_url = url_for("static", filename="fonts/jost.woff2")

    assert "?v=" in js_url, js_url
    assert "?v=" not in font_url, font_url


# --- 3) inlined CSS with url("../ -> /static/ rewrite -------------------------


def test_home_inlines_css_and_rewrites_relative_urls(client: FlaskClient) -> None:
    """styles.css is inlined as a <style> tag (no render-blocking <link>), and its
    relative url("../fonts/…") refs are rewritten to absolute /static/ so they
    still resolve from inside the HTML document."""
    html = client.get("/").get_data(as_text=True)

    assert "<style>" in html
    # The original relative refs must have been rewritten away.
    assert 'url("../' not in html
    assert "url('../" not in html
    # And the rewrite target must be present (proves the inliner ran on styles.css).
    assert "/static/fonts/" in html


def test_inline_css_is_minified_but_intact(client: FlaskClient) -> None:
    """The inlined critical CSS (shipped on every HTML response) is minified — no
    /* */ comments, smaller than the source file — yet still functional: key
    selectors and the rewritten font url survive (a broken minify would 404 fonts
    or drop styles)."""
    import re
    from pathlib import Path

    html = client.get("/").get_data(as_text=True)
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)

    # Comments stripped, and the inlined copy is smaller than the raw stylesheet.
    assert "/*" not in style and "*/" not in style
    raw = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "styles.css").read_text()
    assert len(style) < len(raw)

    # Still intact: design tokens, a hero selector, the new SEO components, and the
    # rewritten (quoted) font url must all survive the minify.
    for token in (":root", ".hero-title", ".breadcrumb", ".cat-chip", "@font-face",
                  "@media", '/static/fonts/jost.woff2'):
        assert token in style, f"minify dropped {token!r}"


# --- 4) long-lived Cache-Control on static assets -----------------------------


def test_static_asset_has_one_year_cache_control(client: FlaskClient) -> None:
    """SEND_FILE_MAX_AGE_DEFAULT = 365 days: static files carry a max-age of ~1y
    (safe because of the ?v= cache-buster), so the CDN/browser caches aggressively."""
    resp = client.get("/static/js/main.js")
    assert resp.status_code == 200

    cache_control = resp.headers.get("Cache-Control", "")
    import re

    match = re.search(r"max-age=(\d+)", cache_control)
    assert match is not None, cache_control
    assert int(match.group(1)) >= 31536000, cache_control


# --- 5) secret-key persistence across "restarts" ------------------------------


def test_secret_key_persists_across_app_rebuilds(tmp_path, monkeypatch) -> None:
    """With SECRET_KEY unset, the key is persisted under DATA_DIR so two app builds
    (i.e. a redeploy) share the SAME key — keeping existing admin sessions valid."""
    data_dir = tmp_path / "data"
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")

    from app.factory import create_app

    app_a = create_app()
    app_b = create_app()

    key_a = app_a.config["SECRET_KEY"]
    key_b = app_b.config["SECRET_KEY"]

    assert key_a, "secret key must not be empty"
    assert key_a == key_b, "redeploy must reuse the persisted key"
    # The key was actually written to disk under DATA_DIR.
    persisted = data_dir / "secret_key"
    assert persisted.exists(), persisted
    assert persisted.read_text(encoding="utf-8").strip() == key_a


# --- 6) SQLite pragmas (WAL + busy_timeout) -----------------------------------


def test_sqlite_pragmas_wal_and_busy_timeout(app: Flask) -> None:
    """The connect-event listener puts SQLite in WAL with a 30s busy timeout, so
    readers don't block the writer and a second writer waits instead of erroring
    with 'database is locked' during a video transcode."""
    from app.factory import db

    with app.app_context():
        journal_mode = db.session.execute(db.text("PRAGMA journal_mode")).scalar()
        busy_timeout = db.session.execute(db.text("PRAGMA busy_timeout")).scalar()

    assert str(journal_mode).lower() == "wal", journal_mode
    assert int(busy_timeout) == 30000, busy_timeout


# --- 7) injected template globals ---------------------------------------------


def test_inject_globals_values(app: Flask) -> None:
    """inject_globals exposes the brand, tagline, instagram URL and the REAL
    current year to every template."""
    with app.app_context():
        # Pull the injected globals exactly as templates receive them.
        merged: dict = {}
        for processor in app.template_context_processors[None]:
            merged.update(processor())

    assert merged["brand"] == "GLÜCK"
    assert merged["tagline"] == "Bolsos minimalistas de cuero vegano"
    assert merged["instagram_url"] == "https://www.instagram.com/gluck_bags/"
    assert merged["current_year"] == datetime.now().year


def test_home_renders_brand_and_instagram(client: FlaskClient) -> None:
    """The injected brand + instagram URL actually surface in the rendered home."""
    html = client.get("/").get_data(as_text=True)

    assert "GLÜCK" in html
    assert "https://www.instagram.com/gluck_bags/" in html
    # The current year (from inject_globals) appears in the footer copyright.
    assert str(datetime.now().year) in html
