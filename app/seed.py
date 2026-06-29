"""One-time seed: import the live catalogue into the DB/media.

Mirrors exactly what production currently shows (gluck.nexttech.com.ar): the same
6 products, in the same order, each with its category and a single cover image.
Production has no prices and no descriptions (the cards link straight to
Instagram), so those fields are seeded as NULL — see the nullable columns on
Product.

Runs on startup only when the products table is empty, so a fresh volume comes
up with the existing catalogue (editable from the admin) instead of blank. The
source images are the full-res originals already shipped under static/.
"""

from __future__ import annotations

import os
import uuid

from flask import current_app

from app.factory import db
from app.models import Media, Product
from app.services import media_service

# (title, category, static image stem) — mirrors the old PRODUCTS list.
_SEED: list[tuple[str, str, str]] = [
    ("Tote Cognac", "Tote", "img/productos/tote-cognac-01"),
    ("Tote Gris", "Tote", "img/productos/tote-gris-interior"),
    ("Crossbody Rosa", "Mini Bag", "img/productos/crossbody-rosa"),
    ("Bandolera Rosa", "Mini Bag", "img/productos/crossbody-rosa-bandolera"),
    ("Clutch Sobre", "Clutch", "img/productos/clutch-rosa-sobre"),
    ("Tote Cognac · Frente", "Tote", "img/productos/tote-cognac-02"),
]


def _find_source(static_dir: str, stem: str) -> str | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        path = os.path.join(static_dir, stem + ext)
        if os.path.exists(path):
            return path
    return None


def seed_initial_products() -> None:
    if os.environ.get("SEED_PRODUCTS", "1") == "0":
        return
    if Product.query.count() > 0:
        return

    static_dir = current_app.static_folder or ""
    for position, (title, category, stem) in enumerate(_SEED):
        source = _find_source(static_dir, stem)
        if not source:
            continue
        product = Product(
            title=title,
            category=category,
            description=None,  # not shown in prod
            price=None,  # not shown in prod ("Consultar")
            is_published=True,
            position=position,
        )
        db.session.add(product)
        db.session.flush()  # assigns product.id

        slug = uuid.uuid4().hex
        info = media_service.process_image(source, product.id, slug)
        media = Media(
            product_id=product.id,
            kind="image",
            path=info["path"],
            width=info["width"],
            height=info["height"],
            widths=info["widths"],
            position=0,
            is_cover=True,
        )
        db.session.add(media)

    db.session.commit()
