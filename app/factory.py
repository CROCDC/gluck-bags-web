import os
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from flask import Flask
from flask_compress import Compress
from flask_sqlalchemy import SQLAlchemy
from markupsafe import Markup

load_dotenv()

# Extension instance defined at module scope so models/repositories can import it
# (`from app.factory import db`) without a circular import.
db = SQLAlchemy()


def _resolve_secret_key(data_dir: str) -> str:
    """Use SECRET_KEY from the env, else a stable key persisted under DATA_DIR.

    Persisting it (instead of a per-process random) keeps admin sessions valid
    across restarts/redeploys without requiring the operator to set anything.
    """
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_path = os.path.join(data_dir, "secret_key")
    try:
        with open(key_path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        key = os.urandom(32).hex()
        try:
            with open(key_path, "w", encoding="utf-8") as fh:
                fh.write(key)
        except OSError:
            pass
        return key


def create_app() -> Flask:
    app = Flask(__name__)

    # Gzip/Brotli text responses (HTML/CSS/JS) at the app level, so compression
    # works regardless of the reverse proxy.
    Compress(app)

    # --- Persistent storage (SQLite DB + uploaded media) ----------------------
    # DATA_DIR is a mounted volume in Docker (/data) so it survives rebuilds;
    # locally it defaults to ./instance (gitignored).
    data_dir = os.path.abspath(os.environ.get("DATA_DIR", "instance"))
    media_root = os.path.join(data_dir, "media")
    os.makedirs(media_root, exist_ok=True)

    app.config["DATA_DIR"] = data_dir
    app.config["MEDIA_ROOT"] = media_root
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(data_dir, 'gluck.db')}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = _resolve_secret_key(data_dir)
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "")
    # Allow large phone videos; they get compressed server-side after upload.
    app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024

    app.config["UMAMI_WEBSITE_ID"] = os.environ.get("UMAMI_WEBSITE_ID")

    db.init_app(app)

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
        # Import here so the models are registered with `db` before create_all,
        # and to avoid import cycles at module load time.
        # Importing these registers the Product/Media models with db.metadata
        # (admin/routes/seed all import app.models), so create_all sees them.
        from app.admin import register_admin
        from app.routes import register_routes
        from app.seed import seed_initial_products

        db.create_all()
        seed_initial_products()

        register_routes(app)
        register_admin(app)

    return app
