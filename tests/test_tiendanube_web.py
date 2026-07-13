"""Route tests for the Tienda Nube HTTP surface: the webhook endpoint and the OAuth
callback helper, plus the /gracias post-purchase page (which clears the cart).

The signed-webhook path reads the secret from the environment, so we set it with
monkeypatch and sign the exact body the test client sends.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.services import cart_service, webhook_service

SECRET = "route-test-secret"


def _sign(raw: bytes) -> str:
    return hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _post(client, raw: bytes, signature: str):
    return client.post(
        "/webhooks/tiendanube",
        data=raw,
        headers={webhook_service.SIGNATURE_HEADER: signature, "Content-Type": "application/json"},
    )


def test_webhook_rejects_bad_signature(client, monkeypatch) -> None:
    monkeypatch.setenv("TN_CLIENT_SECRET", SECRET)
    raw = json.dumps({"event": "product/deleted", "id": 1}).encode()
    resp = _post(client, raw, "not-the-signature")
    assert resp.status_code == 401


def test_webhook_not_configured_without_secret(client, monkeypatch) -> None:
    monkeypatch.delenv("TN_CLIENT_SECRET", raising=False)
    raw = json.dumps({"event": "product/deleted", "id": 1}).encode()
    resp = _post(client, raw, _sign(raw))
    assert resp.status_code == 503


def test_webhook_deleted_ok(client, monkeypatch) -> None:
    monkeypatch.setenv("TN_CLIENT_SECRET", SECRET)
    raw = json.dumps({"event": "product/deleted", "id": 4242}).encode()
    resp = _post(client, raw, _sign(raw))
    assert resp.status_code == 200
    data: dict[str, Any] = resp.get_json()
    assert data["action"] == "deleted" and data["found"] is False


def test_webhook_order_acknowledged(client, monkeypatch) -> None:
    monkeypatch.setenv("TN_CLIENT_SECRET", SECRET)
    raw = json.dumps({"event": "order/paid", "id": 77}).encode()
    resp = _post(client, raw, _sign(raw))
    assert resp.status_code == 200
    assert resp.get_json()["action"] == "acknowledged"


# --- /tn/callback ------------------------------------------------------------


def test_callback_without_code(client) -> None:
    resp = client.get("/tn/callback")
    assert resp.status_code == 200
    assert b"Esperando la autorizaci" in resp.data


def test_callback_with_code_shows_it(client) -> None:
    resp = client.get("/tn/callback?code=abc123")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "abc123" in body
    assert "tn_oauth.py abc123" in body


# --- /gracias ----------------------------------------------------------------


def test_gracias_clears_the_cart(app) -> None:
    c = app.test_client()
    with c.session_transaction() as sess:
        sess[cart_service.CART_SESSION_KEY] = {"1": 2}
    resp = c.get("/gracias")
    assert resp.status_code == 200
    assert "Gracias por tu compra".encode() in resp.data
    with c.session_transaction() as sess:
        assert not sess.get(cart_service.CART_SESSION_KEY)
