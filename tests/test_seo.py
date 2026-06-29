"""Unit tests for the JSON-LD builders in app.seo.

Dimension: structured-data. These dicts drive Google rich results, so the shape
matters: absolute URLs on the canonical origin, the brand, and — critically — an
Offer ONLY when a real price exists (fabricating a price for a 'Consultar' item
risks a manual spam action). No app/server needed; these are pure functions over
the Product model, exercised with lightweight stand-ins.
"""

from __future__ import annotations

from app.seo import (
    BRAND,
    breadcrumb_jsonld,
    organization_jsonld,
    product_jsonld,
    website_jsonld,
)

SITE = "https://gluckbags.com"


class _FakeProduct:
    """Minimal stand-in matching the attributes app.seo reads off Product."""

    def __init__(self, id, title, category=None, price=None, currency="ARS", description=None):
        self.id = id
        self.title = title
        self.category = category
        self.price = price
        self.currency = currency
        self.description = description
        self.cover = None  # no media -> no image key (kept simple)


def test_organization_and_website_absolute_on_canonical() -> None:
    org = organization_jsonld(SITE)
    assert org["@type"] == "Organization"
    assert org["name"] == BRAND
    assert org["url"] == f"{SITE}/"
    assert org["logo"].startswith(f"{SITE}/")
    assert any("instagram.com" in s for s in org["sameAs"])

    site = website_jsonld(SITE)
    assert site["@type"] == "WebSite"
    assert site["url"] == f"{SITE}/"


def test_product_without_price_has_no_offer() -> None:
    data = product_jsonld(_FakeProduct(1, "Tote Cognac", category="Tote"), SITE)
    assert data["@type"] == "Product"
    assert data["name"] == "Tote Cognac"
    assert data["url"] == f"{SITE}/producto/1"
    assert data["brand"]["name"] == BRAND
    assert data["category"] == "Tote"
    assert "offers" not in data  # never fabricate a price


def test_product_with_price_emits_offer() -> None:
    data = product_jsonld(_FakeProduct(2, "Mini Bag", price=45000), SITE)
    offer = data["offers"]
    assert offer["@type"] == "Offer"
    assert offer["price"] == "45000"
    assert offer["priceCurrency"] == "ARS"
    assert offer["availability"].endswith("InStock")
    assert offer["url"] == f"{SITE}/producto/2"


def test_product_description_falls_back_when_missing() -> None:
    data = product_jsonld(_FakeProduct(3, "Clutch Sobre"), SITE)
    assert "Clutch Sobre" in data["description"]
    assert BRAND in data["description"]


def test_breadcrumb_includes_category_node_when_present() -> None:
    crumbs = breadcrumb_jsonld(_FakeProduct(4, "Tote Gris", category="Tote"), SITE)
    items = crumbs["itemListElement"]
    assert [it["name"] for it in items] == ["Inicio", "Tote", "Tote Gris"]
    assert [it["position"] for it in items] == [1, 2, 3]
    # The category node points at the real /categoria/<slug> page.
    assert items[1]["item"] == f"{SITE}/categoria/tote"
    assert items[2]["item"] == f"{SITE}/producto/4"


def test_breadcrumb_omits_category_node_when_absent() -> None:
    crumbs = breadcrumb_jsonld(_FakeProduct(5, "Pieza Suelta"), SITE)
    assert [it["name"] for it in crumbs["itemListElement"]] == ["Inicio", "Pieza Suelta"]
