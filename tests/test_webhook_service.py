"""Tests for the Tienda Nube webhook receiver (app.services.webhook_service).

Signs bodies with a known secret and exercises every branch: signature verification,
unparseable/missing fields, product upsert (via a fake client), product delete, the
gone-from-store (404) case, order acknowledgement and unknown events. No network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

from app.models import TiendaNubeProduct
from app.services import webhook_service
from app.services.tiendanube_client import TiendaNubeError

if TYPE_CHECKING:
    from flask import Flask

SECRET = "shh-super-secret"


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _body(event: str, resource_id: Any) -> bytes:
    return json.dumps({"store_id": 1, "event": event, "id": resource_id}).encode()


class FakeClient:
    """Returns a canned product payload for get_product, or raises."""

    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.fetched: list[int] = []

    def get_product(self, product_id: int) -> dict[str, Any]:
        self.fetched.append(product_id)
        if self.error:
            raise self.error
        return self.payload or {"id": product_id, "name": {"es": f"Bolso {product_id}"}, "variants": []}


# --- signature ---------------------------------------------------------------


def test_verify_signature_accepts_valid() -> None:
    raw = _body("product/updated", 5)
    assert webhook_service.verify_signature(raw, _sign(raw), SECRET) is True


def test_verify_signature_rejects_tampered_body() -> None:
    raw = _body("product/updated", 5)
    sig = _sign(raw)
    assert webhook_service.verify_signature(_body("product/updated", 6), sig, SECRET) is False


def test_verify_signature_rejects_wrong_secret() -> None:
    raw = _body("product/updated", 5)
    assert webhook_service.verify_signature(raw, _sign(raw, "other"), SECRET) is False


def test_verify_signature_rejects_empty() -> None:
    assert webhook_service.verify_signature(b"{}", "", SECRET) is False


# --- process: gating ---------------------------------------------------------


def test_not_configured_without_secret() -> None:
    raw = _body("product/updated", 5)
    body, status = webhook_service.process(raw, _sign(raw), secret=None)
    assert status == 503 and body["reason"] == "not_configured"


def test_bad_signature_is_401() -> None:
    raw = _body("product/updated", 5)
    body, status = webhook_service.process(raw, "deadbeef", secret=SECRET)
    assert status == 401 and body["reason"] == "bad_signature"


def test_unparseable_body_is_400() -> None:
    raw = b"not json"
    body, status = webhook_service.process(raw, _sign(raw), secret=SECRET)
    assert status == 400 and body["reason"] == "bad_body"


def test_missing_fields_is_400() -> None:
    raw = json.dumps({"store_id": 1}).encode()  # no event / id
    body, status = webhook_service.process(raw, _sign(raw), secret=SECRET)
    assert status == 400 and body["reason"] == "bad_body"


# --- process: product events -------------------------------------------------


def test_product_updated_upserts_from_client(app: "Flask") -> None:
    with app.app_context():
        raw = _body("product/updated", 42)
        client = FakeClient(payload={"id": 42, "name": {"es": "Tote"}, "variants": [{"id": 9, "price": "1000"}]})
        body, status = webhook_service.process(raw, _sign(raw), secret=SECRET, client=client)
        assert status == 200 and body["action"] == "upserted" and body["tn_id"] == 42
        assert client.fetched == [42]
        row = TiendaNubeProduct.query.filter_by(tn_id=42).one()
        assert row.name == "Tote"


def test_product_created_upserts(app: "Flask") -> None:
    with app.app_context():
        raw = _body("product/created", 7)
        client = FakeClient(payload={"id": 7, "name": {"es": "Mini"}, "variants": []})
        body, status = webhook_service.process(raw, _sign(raw), secret=SECRET, client=client)
        assert status == 200 and body["action"] == "upserted"
        assert TiendaNubeProduct.query.filter_by(tn_id=7).count() == 1


def test_product_deleted_removes_from_mirror(app: "Flask") -> None:
    with app.app_context():
        from app.factory import db

        db.session.add(TiendaNubeProduct(tn_id=99).apply_payload({"id": 99, "name": {"es": "X"}, "variants": []}))
        db.session.commit()
        raw = _body("product/deleted", 99)
        body, status = webhook_service.process(raw, _sign(raw), secret=SECRET)
        assert status == 200 and body["action"] == "deleted" and body["found"] is True
        assert TiendaNubeProduct.query.filter_by(tn_id=99).count() == 0


def test_product_deleted_unknown_is_ok(app: "Flask") -> None:
    with app.app_context():
        raw = _body("product/deleted", 12345)
        body, status = webhook_service.process(raw, _sign(raw), secret=SECRET)
        assert status == 200 and body["found"] is False


def test_product_updated_404_deletes_mirror(app: "Flask") -> None:
    with app.app_context():
        from app.factory import db

        db.session.add(TiendaNubeProduct(tn_id=8).apply_payload({"id": 8, "name": {"es": "Gone"}, "variants": []}))
        db.session.commit()
        raw = _body("product/updated", 8)
        client = FakeClient(error=TiendaNubeError(404, "not found"))
        body, status = webhook_service.process(raw, _sign(raw), secret=SECRET, client=client)
        assert status == 200 and body["action"] == "deleted"
        assert TiendaNubeProduct.query.filter_by(tn_id=8).count() == 0


def test_product_updated_api_error_is_502(app: "Flask") -> None:
    with app.app_context():
        raw = _body("product/updated", 8)
        client = FakeClient(error=TiendaNubeError(500, "boom"))
        body, status = webhook_service.process(raw, _sign(raw), secret=SECRET, client=client)
        assert status == 502 and body["reason"] == "api_error"


def test_product_updated_without_client_is_202(app: "Flask", monkeypatch) -> None:
    with app.app_context():
        # No injected client and no env credentials -> accept but defer.
        monkeypatch.delenv("TN_STORE_ID", raising=False)
        monkeypatch.delenv("TN_ACCESS_TOKEN", raising=False)
        raw = _body("product/updated", 8)
        body, status = webhook_service.process(raw, _sign(raw), secret=SECRET)
        assert status == 202 and body["reason"] == "no_client"


# --- process: orders + unknown ----------------------------------------------


def test_order_paid_is_acknowledged() -> None:
    raw = _body("order/paid", 555)
    body, status = webhook_service.process(raw, _sign(raw), secret=SECRET)
    assert status == 200 and body["action"] == "acknowledged" and body["order_id"] == 555


def test_unknown_event_is_ignored() -> None:
    raw = _body("category/updated", 1)
    body, status = webhook_service.process(raw, _sign(raw), secret=SECRET)
    assert status == 200 and body["action"] == "ignored"
