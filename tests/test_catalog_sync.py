"""Tests for the Tienda Nube catalogue mirror + sync (headless POC, Fase 2).

Exercises the payload → model mapping (localized fields, lowest price, stock
semantics) and the sync service (upsert, prune, prune-guard) against a fresh
isolated DB, with a fake client so nothing hits the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models import TiendaNubeProduct
from app.models.tiendanube import localized
from app.services.catalog_sync import (
    delete_product,
    sync_products,
    upsert_product,
)

if TYPE_CHECKING:
    from flask import Flask


# --- fixtures / helpers ------------------------------------------------------


class FakeClient:
    """Stands in for TiendaNubeClient: yields the products it was given."""

    def __init__(self, products: list[dict[str, Any]]) -> None:
        self._products = products

    def iter_products(self, **_kwargs: Any):  # noqa: ANN003
        return iter(self._products)


def _payload(tn_id: int, **overrides: Any) -> dict[str, Any]:
    """A realistic Tienda Nube product payload, overridable per field."""
    base: dict[str, Any] = {
        "id": tn_id,
        "name": {"es": f"Bolso {tn_id}", "pt": f"Bolsa {tn_id}"},
        "description": {"es": "Cuero vegano"},
        "handle": {"es": f"bolso-{tn_id}"},
        "canonical_url": f"https://gluck.mitiendanube.com/productos/bolso-{tn_id}/",
        "published": True,
        "categories": [{"id": 9, "name": {"es": "Tote"}}],
        "variants": [
            {"id": 100 + tn_id, "price": "45000.00", "sku": f"SKU{tn_id}", "stock": 3,
             "currency": "ARS", "values": [{"es": "Único"}]},
        ],
        "images": [{"id": 1, "src": f"https://cdn/{tn_id}.jpg"}],
    }
    base.update(overrides)
    return base


# --- localized() -------------------------------------------------------------


def test_localized_picks_language_then_falls_back() -> None:
    assert localized({"es": "Rojo", "pt": "Vermelho"}) == "Rojo"
    assert localized({"pt": "Vermelho"}) == "Vermelho"  # first value fallback
    assert localized({}) == ""
    assert localized(None) == ""
    assert localized("plain") == "plain"


# --- mapping -----------------------------------------------------------------


def test_apply_payload_maps_core_fields(app: "Flask") -> None:
    with app.app_context():
        row = TiendaNubeProduct(tn_id=1).apply_payload(_payload(1))
        assert row.name == "Bolso 1"
        assert row.description == "Cuero vegano"
        assert row.handle == "bolso-1"
        assert row.category == "Tote"
        assert row.published is True
        assert row.canonical_url.endswith("/bolso-1/")
        assert row.images == ["https://cdn/1.jpg"]
        assert row.raw["id"] == 1


def test_lowest_price_and_formatted(app: "Flask") -> None:
    with app.app_context():
        payload = _payload(
            2,
            variants=[
                {"id": 1, "price": "60000.00", "stock": 1},
                {"id": 2, "price": "45000.00", "stock": 2},
            ],
        )
        row = TiendaNubeProduct(tn_id=2).apply_payload(payload)
        assert row.price == "45000.00"  # cheapest variant wins
        assert row.stock == 3  # summed
        assert row.formatted_price == "$ 45.000"
        assert row.in_stock is True


def test_unmanaged_stock_is_none_and_still_in_stock(app: "Flask") -> None:
    with app.app_context():
        payload = _payload(3, variants=[{"id": 1, "price": "1000", "stock": None}])
        row = TiendaNubeProduct(tn_id=3).apply_payload(payload)
        assert row.stock is None  # unmanaged => unlimited, never treated as 0
        assert row.in_stock is True


def test_zero_stock_not_in_stock(app: "Flask") -> None:
    with app.app_context():
        payload = _payload(4, variants=[{"id": 1, "price": "1000", "stock": 0}])
        row = TiendaNubeProduct(tn_id=4).apply_payload(payload)
        assert row.stock == 0
        assert row.in_stock is False


def test_no_priced_variants_leaves_price_none(app: "Flask") -> None:
    with app.app_context():
        row = TiendaNubeProduct(tn_id=5).apply_payload(_payload(5, variants=[]))
        assert row.price is None
        assert row.formatted_price is None


# --- sync_products -----------------------------------------------------------


def test_sync_creates_then_updates_idempotently(app: "Flask") -> None:
    with app.app_context():
        client = FakeClient([_payload(1), _payload(2)])
        first = sync_products(client)
        assert (first.created, first.updated, first.pruned) == (2, 0, 0)
        assert TiendaNubeProduct.query.count() == 2

        # Same store again: everything is an update, nothing created/pruned.
        second = sync_products(FakeClient([_payload(1), _payload(2)]))
        assert (second.created, second.updated, second.pruned) == (0, 2, 0)
        assert TiendaNubeProduct.query.count() == 2


def test_sync_prunes_products_gone_from_store(app: "Flask") -> None:
    with app.app_context():
        sync_products(FakeClient([_payload(1), _payload(2), _payload(3)]))
        result = sync_products(FakeClient([_payload(1)]))  # 2 and 3 removed upstream
        assert result.pruned == 2
        remaining = [r.tn_id for r in TiendaNubeProduct.query.all()]
        assert remaining == [1]


def test_sync_empty_response_does_not_prune(app: "Flask") -> None:
    with app.app_context():
        sync_products(FakeClient([_payload(1), _payload(2)]))
        result = sync_products(FakeClient([]))  # API hiccup / transient empty
        assert result.pruned == 0
        assert TiendaNubeProduct.query.count() == 2  # cache preserved


def test_sync_reflects_updated_fields(app: "Flask") -> None:
    with app.app_context():
        sync_products(FakeClient([_payload(1, published=True)]))
        sync_products(FakeClient([_payload(1, published=False, name={"es": "Nuevo"})]))
        row = TiendaNubeProduct.query.filter_by(tn_id=1).one()
        assert row.published is False
        assert row.name == "Nuevo"


# --- single-product webhook helpers ------------------------------------------


def test_upsert_product_inserts_and_updates(app: "Flask") -> None:
    with app.app_context():
        upsert_product(_payload(7, name={"es": "A"}))
        assert TiendaNubeProduct.query.filter_by(tn_id=7).one().name == "A"
        upsert_product(_payload(7, name={"es": "B"}))
        assert TiendaNubeProduct.query.filter_by(tn_id=7).one().name == "B"
        assert TiendaNubeProduct.query.count() == 1


def test_delete_product(app: "Flask") -> None:
    with app.app_context():
        upsert_product(_payload(8))
        assert delete_product(8) is True
        assert TiendaNubeProduct.query.count() == 0
        assert delete_product(8) is False  # already gone
