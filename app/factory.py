import os
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from flask import Flask
from flask_compress import Compress
from markupsafe import Markup

from app.routes import register_routes

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)

    # Gzip/Brotli text responses (HTML/CSS/JS) at the app level, so compression
    # works regardless of the reverse proxy.
    Compress(app)

    app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "uploads")
    app.config["UMAMI_WEBSITE_ID"] = os.environ.get("UMAMI_WEBSITE_ID")

    # Cache static assets aggressively (1 year). This is safe because every static
    # URL gets a `?v=<mtime>` cache-buster (see _static_cache_buster below), so a
    # changed file gets a new URL and clients re-fetch it. The reverse proxy /
    # Cloudflare respects this Cache-Control instead of its default browser TTL.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = timedelta(days=365)

    @app.url_defaults
    def _static_cache_buster(endpoint: str, values: dict[str, Any]) -> None:
        if endpoint != "static" or "filename" not in values:
            return
        # Don't version fonts: they're also referenced from CSS `url()` WITHOUT a
        # `?v=`, so adding it here would make the <link rel=preload> URL differ from
        # the @font-face URL — the preload would be wasted and the font downloaded
        # twice (which delays the font and, with font-display:swap, the LCP).
        if values["filename"].rsplit(".", 1)[-1].lower() in ("woff2", "woff", "ttf", "otf", "eot"):
            return
        try:
            mtime = os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime
            values["v"] = int(mtime)
        except OSError:
            pass

    @app.context_processor
    def inject_inliners() -> dict[str, Any]:
        def inline_css(filename: str) -> Markup:
            """Inline a static CSS file as a <style> tag (no render-blocking request).
            Relative url("../…") refs are rewritten to absolute /static/ so they
            still resolve once the CSS lives in the HTML document."""
            with open(os.path.join(app.static_folder, filename), encoding="utf-8") as fh:
                css = fh.read()
            css = css.replace('url("../', 'url("/static/').replace("url('../", "url('/static/")
            return Markup(f"<style>{css}</style>")

        return {"inline_css": inline_css}

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "current_year": datetime.now().year,
            "brand": "GLÜCK",
            "tagline": "Bolsos minimalistas de cuero vegano",
            "instagram_url": "https://www.instagram.com/gluck_bags/",
        }

    with app.app_context():
        register_routes(app)

    return app
