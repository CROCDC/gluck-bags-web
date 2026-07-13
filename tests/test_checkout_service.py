"""Tests for the Tienda Nube checkout handoff (app.services.checkout_service).

Uses a fake client (no network) and the real session cart + TN mirror against a
fresh DB. Pins the mapping (cart line -> TN variant) and every branch of
start_checkout: empty, missing email, no-token, unmapped, api error, no url, and
the happy path (with the buyer email forwarded as the draft-order contact) — plus
the post-purchase reconciliation (remember/reconcile/forget).
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
        self.contacts: list[dict[str, Any] | None] = []

    def create_checkout(
        self, line_items: list[dict[str, Any]], *, contact: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append(line_items)
        self.contacts.append(contact)
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


def test_email_required(app: "Flask") -> None:
    """A non-empty cart without a (valid) buyer email never reaches TN: the draft
    order hard-requires contact_email, and it is what prefills the checkout."""
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        for bad_email in (None, "", "   ", "not-an-email"):
            result = checkout_service.start_checkout(contact_email=bad_email, client=FakeClient())
            assert result["ready"] is False
            assert result["reason"] == "email_required"
            assert "email" in result["message"]


def test_not_configured_without_client(app: "Flask", monkeypatch) -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        # No client passed and none from env -> not_configured (an ops regression in
        # prod, so the buyer message is transient, not a DM-coordination pitch).
        monkeypatch.setattr(checkout_service, "build_client_from_env", lambda: None)
        result = checkout_service.start_checkout(contact_email="ana@example.com")
        assert result["reason"] == "not_configured"
        assert "Instagram" not in result["message"]


def test_unmapped_when_product_not_in_mirror(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        result = checkout_service.start_checkout(contact_email="ana@example.com", client=FakeClient())
        assert result["ready"] is False
        assert result["reason"] == "unmapped"
        assert "Tote" in result["unmapped"]


def test_happy_path_returns_redirect_url(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 2)
        _mirror(tn_id=p.id, variant_id=900)  # link the cart product to a TN variant
        client = FakeClient(response={"id": 42, "checkout_url": "https://tn/checkout/42"})

        result = checkout_service.start_checkout(contact_email="ana@example.com", client=client)
        assert result["ready"] is True
        assert result["redirect_url"] == "https://tn/checkout/42"
        assert result["checkout_id"] == 42
        # The resolved line item carried the TN variant id and the cart quantity,
        # and the buyer's email rode along as the draft-order contact.
        assert client.calls == [[{"variant_id": 900, "quantity": 2}]]
        assert client.contacts == [{"contact_email": "ana@example.com"}]


def test_no_url_from_tn(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        _mirror(tn_id=p.id, variant_id=900)
        client = FakeClient(response={"id": 42, "checkout_url": None})
        result = checkout_service.start_checkout(contact_email="ana@example.com", client=client)
        assert result["reason"] == "no_url"


def test_api_error_is_reported(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        _mirror(tn_id=p.id, variant_id=900)
        client = FakeClient(error=TiendaNubeError(500, "boom"))
        result = checkout_service.start_checkout(contact_email="ana@example.com", client=client)
        assert result["ready"] is False
        assert result["reason"] == "api_error"
        assert result["status"] == 500


# --- post-purchase reconciliation ---------------------------------------------


class FakeDraftClient:
    """Returns a canned draft order for get_draft_order (or raises)."""

    def __init__(self, draft: dict[str, Any], error: Exception | None = None) -> None:
        self.draft = draft
        self.error = error
        self.calls: list[Any] = []

    def get_draft_order(self, draft_order_id: Any) -> dict[str, Any]:
        self.calls.append(draft_order_id)
        if self.error:
            raise self.error
        return self.draft


def _pending_with_old_ts(age: int = 600) -> None:
    """Backdate the pending handoff past the min-age/interval throttles (but within
    the TTL, unless `age` says otherwise)."""
    import time

    from flask import session

    pending = session[checkout_service.PENDING_SESSION_KEY]
    session[checkout_service.PENDING_SESSION_KEY] = {
        **pending,
        "ts": int(time.time()) - age,
        "checked": 0,
    }


def test_reconcile_clears_cart_after_completed_checkout(app: "Flask") -> None:
    """TN never redirects the buyer back to /gracias, so the purchased cart must be
    cleared on the next visit once the draft order shows as completed."""
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        checkout_service.remember_pending(42)
        _pending_with_old_ts()

        client = FakeDraftClient({"id": 42, "completed_at": "2026-07-13T10:00:00", "status": "open"})
        assert checkout_service.reconcile_pending(client=client) is True
        assert cart_service.build()["items"] == []

        from flask import session

        assert checkout_service.PENDING_SESSION_KEY not in session


def test_reconcile_keeps_cart_while_draft_open_and_throttles(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        checkout_service.remember_pending(42)
        _pending_with_old_ts()

        client = FakeDraftClient({"id": 42, "completed_at": None, "paid_at": None, "status": "open"})
        assert checkout_service.reconcile_pending(client=client) is False
        assert cart_service.build()["count"] == 1
        # The check is stamped: an immediate second call must NOT hit the API again.
        assert checkout_service.reconcile_pending(client=client) is False
        assert len(client.calls) == 1


def test_reconcile_skips_fresh_handoff(app: "Flask") -> None:
    """Right after the redirect the buyer can't have paid yet — no API call."""
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        checkout_service.remember_pending(42)

        client = FakeDraftClient({"id": 42, "completed_at": "x"})
        assert checkout_service.reconcile_pending(client=client) is False
        assert client.calls == []
        assert cart_service.build()["count"] == 1


def test_reconcile_forgets_cancelled_draft(app: "Flask") -> None:
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        checkout_service.remember_pending(42)
        _pending_with_old_ts()

        client = FakeDraftClient({"id": 42, "status": "cancelled"})
        assert checkout_service.reconcile_pending(client=client) is False
        # The abandoned handoff is dropped, but the cart survives for a retry.
        assert cart_service.build()["count"] == 1

        from flask import session

        assert checkout_service.PENDING_SESSION_KEY not in session


def test_reconcile_without_pending_is_noop(app: "Flask") -> None:
    with app.test_request_context():
        assert checkout_service.reconcile_pending(client=FakeDraftClient({})) is False


def test_reconcile_survives_transport_errors_and_throttles_them(app: "Flask") -> None:
    """A TN network failure must neither 500 the cart read nor bypass the throttle
    (the check is stamped BEFORE the call)."""
    import requests as _requests

    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        checkout_service.remember_pending(42)
        _pending_with_old_ts()

        client = FakeDraftClient({}, error=_requests.ConnectionError("boom"))
        assert checkout_service.reconcile_pending(client=client) is False
        assert checkout_service.reconcile_pending(client=client) is False
        assert len(client.calls) == 1
        assert cart_service.build()["count"] == 1


def test_reconcile_expires_stale_pending(app: "Flask") -> None:
    """A handoff older than the TTL stops being polled forever (TN may purge the
    draft, tokens rotate — the tracking must self-heal)."""
    with app.test_request_context():
        p = ProductRepository.create(title="Tote", price=45000)
        cart_service.add(p.id, 1)
        checkout_service.remember_pending(42)
        _pending_with_old_ts(age=checkout_service._PENDING_TTL + 60)

        client = FakeDraftClient({"id": 42, "completed_at": "x"})
        assert checkout_service.reconcile_pending(client=client) is False
        assert client.calls == []

        from flask import session

        assert checkout_service.PENDING_SESSION_KEY not in session


def test_reconcile_drops_only_purchased_lines(app: "Flask") -> None:
    """Completion removes the handed-off snapshot, never items the buyer added
    after paying on TN."""
    with app.test_request_context():
        bought = ProductRepository.create(title="Tote", price=45000)
        later = ProductRepository.create(title="Clutch", price=30000)
        cart_service.add(bought.id, 2)
        checkout_service.remember_pending(42, cart_service.raw_items())
        cart_service.add(later.id, 1)
        _pending_with_old_ts()

        client = FakeDraftClient({"id": 42, "completed_at": "2026-07-13T10:00:00"})
        assert checkout_service.reconcile_pending(client=client) is True
        items = cart_service.build()["items"]
        assert [(i["id"], i["qty"]) for i in items] == [(later.id, 1)]
