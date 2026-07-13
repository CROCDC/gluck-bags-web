"""Unit tests for the session cart service (app.services.cart_service).

Runs inside a test request context (the cart lives in the Flask session), against a
fresh isolated DB. Covers add/accumulate, qty clamping, absolute set, removal,
subtotal + ARS formatting, and the pruning of items that became unpurchasable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.repositories import ProductRepository
from app.services import cart_service

if TYPE_CHECKING:
    from flask import Flask


def test_add_accumulates_and_builds(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        cart_service.add(p.id, 1)
        cart = cart_service.build()
        assert cart["count"] == 2
        assert cart["subtotal"] == 90000
        assert cart["subtotal_formatted"] == "$ 90.000"
        item = cart["items"][0]
        assert item["title"] == "Tote"
        assert item["qty"] == 2
        assert item["line_total_formatted"] == "$ 90.000"


def test_qty_is_clamped_to_max(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Mini", price=1000)
        cart_service.add(p.id, 999)
        assert cart_service.count() == cart_service.MAX_QTY


def test_set_qty_absolute_and_zero_removes(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Bucket", price=5000)
        cart_service.add(p.id, 3)
        cart_service.set_qty(p.id, 1)
        assert cart_service.count() == 1
        cart_service.set_qty(p.id, 0)
        assert cart_service.build()["items"] == []


def test_remove_and_clear(app: "Flask") -> None:
    with app.test_request_context():
        a = ProductRepository.create(title="A", price=1000)
        b = ProductRepository.create(title="B", price=2000)
        cart_service.add(a.id, 1)
        cart_service.add(b.id, 1)
        cart_service.remove(a.id)
        assert cart_service.count() == 1
        cart_service.clear()
        assert cart_service.count() == 0


def test_build_prunes_unpublished_product(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Oculto", price=1000)
        cart_service.add(p.id, 1)
        p.is_published = False
        ProductRepository.save()
        cart = cart_service.build()
        assert cart["items"] == []
        assert cart["count"] == 0


def test_build_prunes_product_that_lost_its_price(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="SinPrecio", price=1000)
        cart_service.add(p.id, 1)
        p.price = None
        ProductRepository.save()
        assert cart_service.build()["items"] == []


def test_build_prunes_deleted_product(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Borrado", price=1000)
        cart_service.add(p.id, 2)
        ProductRepository.delete(p)
        assert cart_service.build()["items"] == []


def test_is_purchasable(app: "Flask") -> None:
    with app.test_request_context():
        priced = ProductRepository.create(title="Ok", price=1000)
        unpriced = ProductRepository.create(title="Consultar", price=None)
        hidden = ProductRepository.create(title="Draft", price=1000, is_published=False)
        assert cart_service.is_purchasable(priced) is True
        assert cart_service.is_purchasable(unpriced) is False
        assert cart_service.is_purchasable(hidden) is False
        assert cart_service.is_purchasable(None) is False


def test_format_ars() -> None:
    assert cart_service.format_ars(0) == "$ 0"
    assert cart_service.format_ars(45000) == "$ 45.000"
    assert cart_service.format_ars(1234567) == "$ 1.234.567"
