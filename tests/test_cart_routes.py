"""Integration tests for the cart HTTP surface (app.cart).

Drives the JSON API, the /carrito page and the /checkout seam through the Flask
test client (the session cookie carries the cart across requests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.repositories import ProductRepository

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient


def _make_product(app: "Flask", **kwargs) -> int:
    with app.app_context():
        p = ProductRepository.create(
            title=kwargs.get("title", "Tote"),
            price=kwargs.get("price", 45000),
            is_published=kwargs.get("is_published", True),
        )
        return p.id


# --- API ---------------------------------------------------------------------


def test_get_cart_starts_empty(client: "FlaskClient") -> None:
    res = client.get("/api/cart")
    assert res.status_code == 200
    data = res.get_json()
    assert data["items"] == []
    assert data["count"] == 0
    assert data["subtotal"] == 0


def test_add_update_remove_flow(app: "Flask", client: "FlaskClient") -> None:
    pid = _make_product(app, title="Tote", price=45000)

    add = client.post("/api/cart/add", json={"product_id": pid, "qty": 1})
    assert add.status_code == 200
    assert add.get_json()["count"] == 1
    assert add.get_json()["subtotal"] == 45000

    upd = client.post("/api/cart/update", json={"product_id": pid, "qty": 3})
    assert upd.get_json()["count"] == 3
    assert upd.get_json()["subtotal"] == 135000

    rem = client.post("/api/cart/remove", json={"product_id": pid})
    assert rem.get_json()["count"] == 0


def test_add_rejects_unpurchasable_product(app: "Flask", client: "FlaskClient") -> None:
    unpriced = _make_product(app, title="Consultar", price=None)
    res = client.post("/api/cart/add", json={"product_id": unpriced})
    assert res.status_code == 409

    missing = client.post("/api/cart/add", json={"product_id": 999999})
    assert missing.status_code == 409


def test_add_requires_product_id(client: "FlaskClient") -> None:
    assert client.post("/api/cart/add", json={}).status_code == 400


def test_clear_empties_cart(app: "Flask", client: "FlaskClient") -> None:
    pid = _make_product(app, price=1000)
    client.post("/api/cart/add", json={"product_id": pid, "qty": 2})
    res = client.post("/api/cart/clear")
    assert res.get_json()["count"] == 0


def test_cart_persists_across_requests(app: "Flask", client: "FlaskClient") -> None:
    pid = _make_product(app, price=1000)
    client.post("/api/cart/add", json={"product_id": pid, "qty": 2})
    # A separate GET on the same client (same session cookie) still sees it.
    assert client.get("/api/cart").get_json()["count"] == 2


# --- page --------------------------------------------------------------------


def test_cart_page_empty(client: "FlaskClient") -> None:
    res = client.get("/carrito")
    assert res.status_code == 200
    assert "vac" in res.get_data(as_text=True).lower()  # "vacío"


def test_cart_page_with_item(app: "Flask", client: "FlaskClient") -> None:
    pid = _make_product(app, title="Tote Cognac", price=45000)
    client.post("/api/cart/add", json={"product_id": pid, "qty": 1})
    html = client.get("/carrito").get_data(as_text=True)
    assert "Tote Cognac" in html
    assert "$ 45.000" in html
    assert 'name="robots" content="noindex' in html


# --- checkout seam -----------------------------------------------------------


def test_checkout_empty_cart_is_rejected(client: "FlaskClient") -> None:
    res = client.post("/checkout")
    assert res.status_code == 400
    assert res.get_json()["ready"] is False
    assert res.get_json()["reason"] == "empty"


def test_checkout_requires_buyer_email(app: "Flask", client: "FlaskClient") -> None:
    """The buyer email is what TN prefills at the hosted checkout — without it the
    handoff is rejected (400) before ever calling TN."""
    pid = _make_product(app, price=1000)
    client.post("/api/cart/add", json={"product_id": pid, "qty": 1})
    res = client.post("/checkout", json={})
    assert res.status_code == 400
    data = res.get_json()
    assert data["ready"] is False
    assert data["reason"] == "email_required"
    assert "email" in data["message"]


def test_checkout_reports_not_configured(app: "Flask", client: "FlaskClient") -> None:
    pid = _make_product(app, price=1000)
    client.post("/api/cart/add", json={"product_id": pid, "qty": 1})
    data = client.post("/checkout", json={"email": "ana@example.com"}).get_json()
    assert data["ready"] is False
    assert data["reason"] == "not_configured"
    assert "message" in data


def test_checkout_redirects_when_tn_ready(app: "Flask", client: "FlaskClient", monkeypatch) -> None:
    """With TN configured and the product linked in the mirror, /checkout returns a
    redirect_url the frontend can send the buyer to."""
    from app.factory import db
    from app.models import TiendaNubeProduct
    from app.services import checkout_service

    pid = _make_product(app, title="Tote", price=45000)
    with app.app_context():
        db.session.add(
            TiendaNubeProduct(tn_id=pid).apply_payload(
                {"id": pid, "name": {"es": "Tote"}, "variants": [{"id": 900, "price": "45000"}]}
            )
        )
        db.session.commit()

    class _Client:
        def create_checkout(self, line_items, *, contact=None):
            assert line_items == [{"variant_id": 900, "quantity": 1}]
            assert contact == {"contact_email": "ana@example.com"}
            return {"id": 7, "checkout_url": "https://tn/checkout/7"}

    monkeypatch.setattr(checkout_service, "build_client_from_env", lambda: _Client())

    client.post("/api/cart/add", json={"product_id": pid, "qty": 1})
    data = client.post("/checkout", json={"email": "ana@example.com"}).get_json()
    assert data["ready"] is True
    assert data["redirect_url"] == "https://tn/checkout/7"

    with client.session_transaction() as sess:
        assert sess[checkout_service.PENDING_SESSION_KEY]["id"] == 7


# --- /gracias ------------------------------------------------------------------


def test_gracias_consumes_pending_handoff(app: "Flask", client: "FlaskClient") -> None:
    """A session that handed a checkout to TN gets its purchased lines dropped."""
    from app.services import checkout_service

    pid = _make_product(app, title="Tote", price=45000)
    client.post("/api/cart/add", json={"product_id": pid, "qty": 1})
    with client.session_transaction() as sess:
        sess[checkout_service.PENDING_SESSION_KEY] = {
            "id": 42, "ts": 0, "checked": 0, "items": {str(pid): 1},
        }

    assert client.get("/gracias").status_code == 200
    assert client.get("/api/cart").get_json()["count"] == 0
    with client.session_transaction() as sess:
        assert checkout_service.PENDING_SESSION_KEY not in sess


def test_gracias_never_wipes_a_visitors_cart(app: "Flask", client: "FlaskClient") -> None:
    """Anyone can open /gracias (history, a shared link): without a pending TN
    handoff the in-progress cart must survive."""
    pid = _make_product(app, title="Tote", price=45000)
    client.post("/api/cart/add", json={"product_id": pid, "qty": 2})

    assert client.get("/gracias").status_code == 200
    assert client.get("/api/cart").get_json()["count"] == 2
