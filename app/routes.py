from typing import Any

from flask import Flask, Response, abort, render_template, send_from_directory

from app.repositories import ProductRepository

# --- Static site content (the non-product sections of the landing page) -------

CATEGORIES: list[dict[str, str]] = [
    {
        "name": "Tote",
        "tagline": "Para todos los días",
        "image": "img/productos/tote-cognac-01",
    },
    {
        "name": "Mini Bag",
        "tagline": "Lo esencial, en pequeño",
        "image": "img/productos/crossbody-rosa",
    },
    {
        "name": "Bucket Bag",
        "tagline": "Volumen y carácter",
        "image": "img/video-posters/bucket-bag-reveal",
    },
    {
        "name": "Clutch",
        "tagline": "Lo justo y necesario",
        "image": "img/productos/clutch-rosa-sobre",
    },
]


def register_routes(app: Flask) -> None:
    @app.route("/")
    def index() -> str:
        context: dict[str, Any] = {
            "categories": CATEGORIES,
            "products": ProductRepository.get_published(),
        }
        return render_template("index.html", **context)

    @app.route("/producto/<int:product_id>")
    def product_detail(product_id: int) -> str:
        product = ProductRepository.get_by_id(product_id)
        if product is None or not product.is_published:
            abort(404)
        return render_template("product_detail.html", product=product)

    @app.route("/media/<path:filename>")
    def media_file(filename: str) -> Response:
        # Uploaded media is immutable (new upload = new path), so cache forever.
        resp = send_from_directory(app.config["MEDIA_ROOT"], filename, conditional=True)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.route("/robots.txt")
    def robots() -> Response:
        return send_from_directory(app.static_folder, "robots.txt")
