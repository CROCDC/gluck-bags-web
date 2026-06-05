import os
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from flask import Flask

from app.routes import register_routes

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)

    app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "uploads")
    app.config["UMAMI_WEBSITE_ID"] = os.environ.get("UMAMI_WEBSITE_ID")

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
