from typing import Any

from flask import Flask, Response, render_template, send_from_directory

# --- Site content (single source of truth for the landing page) ---------------

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

PRODUCTS: list[dict[str, str]] = [
    {
        "name": "Tote Cognac",
        "category": "Tote",
        "image": "img/productos/tote-cognac-01",
    },
    {
        "name": "Tote Gris",
        "category": "Tote",
        "image": "img/productos/tote-gris-interior",
    },
    {
        "name": "Crossbody Rosa",
        "category": "Mini Bag",
        "image": "img/productos/crossbody-rosa",
    },
    {
        "name": "Bandolera Rosa",
        "category": "Mini Bag",
        "image": "img/productos/crossbody-rosa-bandolera",
    },
    {
        "name": "Clutch Sobre",
        "category": "Clutch",
        "image": "img/productos/clutch-rosa-sobre",
    },
    {
        "name": "Tote Cognac · Frente",
        "category": "Tote",
        "image": "img/productos/tote-cognac-02",
    },
]


def register_routes(app: Flask) -> None:
    @app.route("/")
    def index() -> str:
        context: dict[str, Any] = {
            "categories": CATEGORIES,
            "products": PRODUCTS,
        }
        return render_template("index.html", **context)

    @app.route("/robots.txt")
    def robots() -> Response:
        return send_from_directory(app.static_folder, "robots.txt")
