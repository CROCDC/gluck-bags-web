import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request
from flask_compress import Compress
from flask_sqlalchemy import SQLAlchemy
from markupsafe import Markup
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

load_dotenv()

# Extension instance defined at module scope so models/repositories can import it
# (`from app.factory import db`) without a circular import.
db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection: object, connection_record: object) -> None:
    """WAL + a long busy timeout for SQLite: readers never block the writer, and a
    second writer waits up to 30s instead of failing with 'database is locked'
    (relevant while a video transcode briefly holds the write lock)."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    except Exception:  # noqa: BLE001 — non-sqlite backends ignore these PRAGMAs
        pass
    finally:
        cursor.close()


def _initialize_schema(data_dir: str, seed_fn: "object") -> None:
    """Create tables + seed exactly once, even when multiple gunicorn workers boot
    against a fresh volume at the same time. A cross-process file lock serializes
    them so they don't race db.create_all() ('table already exists') or double-seed."""
    lock_fh = None
    try:
        import fcntl

        lock_fh = open(os.path.join(data_dir, ".init.lock"), "w")
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
    except (ImportError, OSError):
        lock_fh = None
    try:
        try:
            db.create_all()
        except OperationalError:
            db.session.rollback()  # another worker won the race; tables already exist
        seed_fn()
    finally:
        if lock_fh is not None:
            try:
                import fcntl

                fcntl.flock(lock_fh, fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
            lock_fh.close()


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


def _register_canonical_host(app: Flask) -> None:
    """Enforce one canonical host + HTTPS when FORCE_CANONICAL_HOST is on (prod).

    The site answers on three hosts (apex, www, and the nexttech alias) and over
    plain http; without this every variant is a crawlable duplicate and the canonical
    domain leaks. We 301 any non-canonical host/scheme to SITE_URL (preserving path +
    query) and add HSTS. Gated by a flag so the local/test client (host 'localhost')
    is never redirected."""
    if not app.config["FORCE_CANONICAL_HOST"]:
        return

    canonical = urlsplit(app.config["SITE_URL"])
    canonical_host = canonical.netloc
    canonical_scheme = canonical.scheme or "https"

    @app.before_request
    def _redirect_to_canonical() -> Any:
        # Behind nginx-proxy/Cloudflare the original scheme arrives in this header.
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        if request.host == canonical_host and scheme == canonical_scheme:
            return None
        target = urlunsplit(
            (canonical_scheme, canonical_host, request.path, request.query_string.decode(), "")
        )
        return redirect(target, code=301)

    @app.after_request
    def _hsts(response: Any) -> Any:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        return response


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
    # Wait (don't fail) if the DB is briefly write-locked by another worker.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 30}}
    app.config["SECRET_KEY"] = _resolve_secret_key(data_dir)
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "")
    # Cap uploads. Phone clips fit comfortably; the file is compressed server-side
    # after upload. Kept modest so a single request can't fill the container disk.
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

    # Session-cookie hardening. SameSite=Lax is the CSRF defense for the admin: the
    # session cookie is NOT sent on cross-site POSTs, so a forged form from another
    # site can't act on a logged-in admin (all state changes are POST). HttpOnly
    # keeps JS from reading it. Secure is enabled in production (the site is HTTPS
    # behind Cloudflare) via SESSION_COOKIE_SECURE=1; it stays off locally/in tests
    # so the cookie still works over plain http.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") in (
        "1",
        "true",
        "True",
    )

    app.config["UMAMI_WEBSITE_ID"] = os.environ.get("UMAMI_WEBSITE_ID")

    # Public/canonical origin for absolute OG, Twitter, canonical and sitemap URLs
    # (no trailing slash). Defaults to the real apex domain; overridable per env.
    app.config["SITE_URL"] = os.environ.get("SITE_URL", "https://gluckbags.com").rstrip("/")

    # When on (production), the app enforces a single canonical host: every request
    # on another host (www, the nexttech alias) or over http is 301-redirected to
    # SITE_URL, and responses carry HSTS. Off by default so the local/test client
    # (host "localhost") is never redirected.
    app.config["FORCE_CANONICAL_HOST"] = os.environ.get("FORCE_CANONICAL_HOST", "0") in (
        "1",
        "true",
        "True",
    )
    _register_canonical_host(app)

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
            # Public/canonical origin for absolute OG, Twitter and canonical URLs.
            "site_url": app.config["SITE_URL"],
        }

    with app.app_context():
        # Import here so the models are registered with `db` before create_all,
        # and to avoid import cycles at module load time.
        # Importing these registers the Product/Media models with db.metadata
        # (admin/routes/seed all import app.models), so create_all sees them.
        from app.admin import register_admin
        from app.routes import register_routes
        from app.seed import seed_initial_products

        _initialize_schema(data_dir, seed_initial_products)

        register_routes(app)
        register_admin(app)

    @app.errorhandler(404)
    def _not_found(error: object) -> tuple[str, int]:
        # Branded, indexable-safe (noindex) 404 in Spanish, with nav back to the site.
        return render_template("404.html"), 404

    @app.errorhandler(413)
    def _too_large(error: object) -> tuple[str, int]:
        return "El archivo es demasiado grande.", 413

    @app.errorhandler(500)
    def _server_error(error: object) -> tuple[str, int]:
        db.session.rollback()
        return "Ocurrió un error al procesar la solicitud. Probá de nuevo.", 500

    return app
