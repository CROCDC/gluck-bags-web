"""Tests for the storefront catalogue swap (app.services.catalog).

Proves the CATALOG_SOURCE=tiendanube path end to end: the facade returns adapted
mirror products, every storefront page renders them unchanged (home, product detail,
category, sitemap), and — the point of the swap — a cart built from the TN storefront
carries TN ids so the checkout handoff resolves them to variants and returns a
redirect. Default (admin) behaviour is covered by the existing suites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.factory import db
from app.models import TiendaNubeProduct
from app.services import catalog

if TYPE_CHECKING:
    from flask import Flask


def _payload(tn_id: int, name: str, price: int, *, category: str = "Tote",
             published: bool = True, stock: int | None = 5, variant_id: int | None = None) -> dict[str, Any]:
    return {
        "id": tn_id,
        "name": {"es": name},
        "handle": {"es": name.lower().replace(" ", "-")},
        "description": {"es": f"Bolso {name} de cuero vegano."},
        "published": published,
        "canonical_url": f"https://tienda.example/{tn_id}",
        "categories": [{"id": 1, "name": {"es": category}}],
        "variants": [{"id": variant_id or tn_id * 10, "price": str(price), "stock": stock, "currency": "ARS"}],
        "images": [{"id": tn_id * 100, "src": f"https://cdn.example/{tn_id}.jpg", "position": 1, "width": 1080, "height": 1350}],
    }


def _seed(app: "Flask", *payloads: dict[str, Any]) -> None:
    with app.app_context():
        for payload in payloads:
            db.session.add(TiendaNubeProduct(tn_id=int(payload["id"])).apply_payload(payload))
        db.session.commit()


def _tn(app: "Flask") -> "Flask":
    """Flip the app to the Tienda Nube storefront source."""
    app.config["CATALOG_SOURCE"] = "tiendanube"
    return app


# --- source selection --------------------------------------------------------


def test_source_defaults_to_admin(app: "Flask") -> None:
    with app.app_context():
        assert catalog.source() == "admin"
        assert catalog.is_tiendanube() is False


def test_source_reads_config(app: "Flask") -> None:
    _tn(app)
    with app.app_context():
        assert catalog.is_tiendanube() is True


# --- facade over the mirror --------------------------------------------------


def test_get_published_returns_only_published_mirror(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote Cognac", 45000), _payload(2, "Mini Rosa", 30000, category="Mini Bag"),
          _payload(3, "Oculto", 10000, published=False))
    _tn(app)
    with app.app_context():
        published = catalog.get_published()
        assert {p.id for p in published} == {1, 2}
        assert all(isinstance(p, catalog.StorefrontProduct) for p in published)


def test_adapter_exposes_product_interface(app: "Flask") -> None:
    _seed(app, _payload(5, "Tote Cognac", 45000))
    _tn(app)
    with app.app_context():
        p = catalog.get_by_id(5)
        assert p.id == 5
        assert p.title == "Tote Cognac"
        assert p.category == "Tote"
        assert p.price == 45000  # int pesos, from the "45000" variant string
        assert p.formatted_price == "$ 45.000"
        assert p.currency == "ARS"
        assert p.is_published is True
        assert p.in_stock is True
        assert p.cover.src == "https://cdn.example/5.jpg"
        assert p.cover.width == 1080 and p.cover.height == 1350
        assert len(p.images) == 1 and p.videos == []
        assert catalog.is_purchasable(p) is True


def test_adapter_out_of_stock_is_not_purchasable(app: "Flask") -> None:
    _seed(app, _payload(6, "Agotado", 45000, stock=0))
    _tn(app)
    with app.app_context():
        p = catalog.get_by_id(6)
        assert p.in_stock is False
        assert catalog.is_purchasable(p) is False


def test_published_categories_from_mirror(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote", 45000, category="Tote"), _payload(2, "Mini", 30000, category="Mini Bag"))
    _tn(app)
    with app.app_context():
        assert catalog.published_categories() == ["Tote", "Mini Bag"]


# --- storefront pages render the mirror --------------------------------------


def test_home_renders_mirror_products(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote Cognac", 45000))
    _tn(app)
    html = app.test_client().get("/").get_data(as_text=True)
    assert "Tote Cognac" in html
    assert "$ 45.000" in html
    assert "https://cdn.example/1.jpg" in html


def test_product_detail_renders_mirror_product(app: "Flask") -> None:
    _seed(app, _payload(7, "Bucket Negro", 52000, category="Bucket Bag"))
    _tn(app)
    resp = app.test_client().get("/producto/7")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Bucket Negro" in html
    # The add-to-cart button carries the TN product id — the loop's linchpin.
    assert 'data-add-to-cart="7"' in html


def test_unpublished_mirror_product_is_404(app: "Flask") -> None:
    _seed(app, _payload(8, "Oculto", 10000, published=False))
    _tn(app)
    assert app.test_client().get("/producto/8").status_code == 404


def test_category_page_renders_mirror(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote Cognac", 45000, category="Tote"))
    _tn(app)
    resp = app.test_client().get("/categoria/tote")
    assert resp.status_code == 200
    assert "Tote Cognac" in resp.get_data(as_text=True)


def test_sitemap_lists_mirror_products(app: "Flask") -> None:
    _seed(app, _payload(11, "Tote", 45000), _payload(12, "Mini", 30000, category="Mini Bag"))
    _tn(app)
    xml = app.test_client().get("/sitemap.xml").get_data(as_text=True)
    assert "/producto/11" in xml
    assert "/producto/12" in xml


# --- the closed loop: TN storefront -> cart -> checkout ----------------------


def test_cart_add_and_checkout_loop(app: "Flask", monkeypatch) -> None:
    """Add a TN product to the cart and run the checkout handoff: the cart line's id
    is the TN product id, so the resolver maps it to variant 950 with no extra link."""
    _seed(app, _payload(95, "Tote Cognac", 45000, variant_id=950))
    _tn(app)
    client = app.test_client()

    add = client.post("/api/cart/add", json={"product_id": 95, "qty": 1})
    assert add.status_code == 200
    assert add.get_json()["count"] == 1

    captured: dict[str, Any] = {}

    class _TNClient:
        def create_checkout(self, line_items):
            captured["line_items"] = line_items
            return {"id": 3, "checkout_url": "https://checkout.tiendanube/3"}

    from app.services import checkout_service

    monkeypatch.setattr(checkout_service, "build_client_from_env", lambda: _TNClient())

    data = client.post("/checkout").get_json()
    assert data["ready"] is True
    assert data["redirect_url"] == "https://checkout.tiendanube/3"
    # The resolver turned the TN product id (95) into its first variant (950).
    assert captured["line_items"] == [{"variant_id": 950, "quantity": 1}]


def test_cart_rejects_unpublished_mirror_product(app: "Flask") -> None:
    _seed(app, _payload(20, "Oculto", 45000, published=False))
    _tn(app)
    resp = app.test_client().post("/api/cart/add", json={"product_id": 20})
    assert resp.status_code == 409
