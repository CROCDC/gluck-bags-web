"""Schema.org JSON-LD builders for rich results.

Plain dicts so they're unit-testable; routes serialize them with `json.dumps` and
the templates emit `<script type="application/ld+json">`. Kept faithful to the
visible page: no invented price (the catalogue sells via Instagram and shows
"Consultar"), so `offers` is emitted only when a real price exists — fabricated
markup risks a manual spam action.
"""

from __future__ import annotations

import json
from typing import Any

from app.models import Product
from app.utils import slugify

BRAND = "GLÜCK"
_LOGO = "/static/img/marca/avatar-perfil-gluck.jpg"
INSTAGRAM = "https://www.instagram.com/gluck_bags/"


def dump_jsonld(obj: Any) -> str:
    """Serialize JSON-LD for safe embedding in a <script type="application/ld+json">.

    json.dumps escapes quotes/backslashes but NOT '<', '>' or '&', so a string
    containing '</script>' (e.g. a product description) would break out of the
    element — an XSS vector. Escape those (and the JS line separators) as JSON
    \\uXXXX, which is still valid JSON the parser decodes back to the originals."""
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def organization_jsonld(site_url: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": BRAND,
        "url": f"{site_url}/",
        "logo": f"{site_url}{_LOGO}",
        "sameAs": [INSTAGRAM],
    }


def website_jsonld(site_url: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": BRAND,
        "url": f"{site_url}/",
    }


def _cover_image(product: Product, site_url: str) -> str | None:
    cover = product.cover
    if cover is None:
        return None
    if cover.is_image and cover.default_image_url:
        return f"{site_url}{cover.default_image_url}"
    if cover.is_video:
        return f"{site_url}{cover.poster_url}"
    return None


def product_jsonld(product: Product, site_url: str) -> dict[str, Any]:
    url = f"{site_url}/producto/{product.id}"
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product.title,
        "url": url,
        "brand": {"@type": "Brand", "name": BRAND},
        "description": product.description
        or f"{product.title} — {BRAND}, cartera de cuero vegano hecha a mano.",
    }
    image = _cover_image(product, site_url)
    if image:
        data["image"] = [image]
    if product.category:
        data["category"] = product.category
    # Only emit an Offer when there is a real price — never fabricate one.
    if product.price is not None:
        data["offers"] = {
            "@type": "Offer",
            "price": str(product.price),
            "priceCurrency": product.currency,
            "availability": "https://schema.org/InStock",
            "url": url,
        }
    return data


def breadcrumb_jsonld(product: Product, site_url: str) -> dict[str, Any]:
    """Inicio › <categoría> › <producto>. The category node is included only when
    the product has a category (it links to the real /categoria/<slug> page)."""
    items: list[dict[str, Any]] = [{"name": "Inicio", "url": f"{site_url}/"}]
    if product.category:
        items.append(
            {"name": product.category, "url": f"{site_url}/categoria/{slugify(product.category)}"}
        )
    items.append({"name": product.title, "url": f"{site_url}/producto/{product.id}"})
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": it["name"], "item": it["url"]}
            for i, it in enumerate(items)
        ],
    }
