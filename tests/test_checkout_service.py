"""Tests for the Tienda Nube checkout handoff (app.services.checkout_service).

Uses a fake client (no network) and the real session cart + TN mirror against a
fresh DB. Pins the mapping (cart line -> TN variant) and every branch of
start_checkout: empty, no-token, unmapped, api error, no url, and the happy path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models import TiendaNubeProduct
from app.repositories import ProductRepository
from app.services import cart_service, checkout_service
from app.services.tiendanube_client import TiendaNubeError

if TYPE_CHECKING:
    from flask import Flask


class FakeClient:
    """Captures create_checkout input and returns a canned response (or raises)."""

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.response = response or {"id": 1, "checkout_url": "https://tn/checkout/1"}
        self.error = error
        self.calls: list[list[dict[str, Any]]] = []

    def create_checkout(self, line_items: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(line_items)
        if self.error:
            raise self.error
        return self.response


def _mirror(tn_id: int, variant_id: int) -> None:
    """Insert a TN mirror product whose first variant has `variant_id`."""
    payload = {
        "id": tn_id,
        "name": {"es": f"Bolso {tn_id}"},
        "variants": [{"id": variant_id, "price": "1000", "stock": 5}],
    }
    from app.factory import db

    db.session.add(TiendaNubeProduct(tn_id=tn_id).apply_payload(payload))
    db.session.commit()


# --- resolver ----------------------------------------------------------------


def test_mirror_resolver_maps_to_first_variant(app: "Flask") -> None:
    with app.test_request_context():
        _mirror(tn_id=7, variant_id=900)
        out = checkout_service.mirror_variant_resolver({"id": 7, "qty": 2})
        assert out == {"variant_id": 900, "quantity": 2}


def test_mirror_resolver_unknown_product_returns_none(app: "Flask") -> None:
    with app.test_request_context():
        assert checkout_service.mirror_variant_resolver({"id": 123, "qty": 1}) is None


# --- start_checkout branches -------------------------------------------------


def test_empty_cart(app: "Flask") -> None:
    with app.test_request_context():
        result = checkout_service.start_checkout(client=FakeClient())
        assert result["ready"] is False
        assert result["reason"] == "empty"


def test_integration_pending_without_client(app: "Flask", monkeypatch) -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        # No client passed and none from env -> integration_pending.
        monkeypatch.setattr(checkout_service, "build_client_from_env", lambda: None)
        result = checkout_service.start_checkout()
        assert result["reason"] == "integration_pending"


def test_unmapped_when_product_not_in_mirror(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        result = checkout_service.start_checkout(client=FakeClient())
        assert result["ready"] is False
        assert result["reason"] == "unmapped"
        assert "Tote" in result["unmapped"]


def test_happy_path_returns_redirect_url(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 2)
        _mirror(tn_id=p.id, variant_id=900)  # link the cart product to a TN variant
        client = FakeClient(response={"id": 42, "checkout_url": "https://tn/checkout/42"})

        result = checkout_service.start_checkout(client=client)
        assert result["ready"] is True
        assert result["redirect_url"] == "https://tn/checkout/42"
        assert result["checkout_id"] == 42
        # The resolved line item carried the TN variant id and the cart quantity.
        assert client.calls == [[{"variant_id": 900, "quantity": 2}]]


def test_no_url_from_tn(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        _mirror(tn_id=p.id, variant_id=900)
        client = FakeClient(response={"id": 42, "checkout_url": None})
        result = checkout_service.start_checkout(client=client)
        assert result["reason"] == "no_url"


def test_api_error_is_reported(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        _mirror(tn_id=p.id, variant_id=900)
        client = FakeClient(error=TiendaNubeError(500, "boom"))
        result = checkout_service.start_checkout(client=client)
        assert result["ready"] is False
        assert result["reason"] == "api_error"
        assert result["status"] == 500
