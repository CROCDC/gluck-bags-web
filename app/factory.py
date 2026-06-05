import os
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from flask import Flask
from flask_compress import Compress

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
        try:
            mtime = os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime
            values["v"] = int(mtime)
        except OSError:
            pass

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
