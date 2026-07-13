"""Tienda Nube webhook receiver (headless POC, Fase 3 — remaining).

Tienda Nube notifies our BFF of catalogue/order changes by POSTing a **minimal**
JSON body — ``{"store_id", "event", "id"}`` — not the full resource. So a
``product/updated`` tells us *which* product changed and we fetch the fresh payload
from the API to refresh our mirror (see app/services/catalog_sync.py). Orders are
acknowledged (they drive backend reconciliation/analytics, decoupled from the
buyer's browser session).

Security: every request is signed with HMAC-SHA256 of the **raw** body, keyed by the
app's client secret, in the ``x-linkedstore-hmac-sha256`` header. We verify it with a
constant-time compare before trusting anything. An unsigned/forged request is a 401;
a body we can't parse is a 400.

Like the REST client, the exact header name and payload keys are the ones documented
for Tienda Nube but couldn't be exercised against the live API from the build
environment — they're isolated here behind small seams so a mismatch is a one-line
fix, and our dispatch/verification logic is what the tests pin down.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Optional

from app.services import catalog_sync
from app.services.tiendanube_client import TiendaNubeClient, TiendaNubeError

# Header carrying the HMAC-SHA256 signature of the raw request body.
SIGNATURE_HEADER = "x-linkedstore-hmac-sha256"

# Events we act on. Anything else is acknowledged and ignored (a 200 so Tienda Nube
# doesn't retry a webhook we simply don't care about).
_PRODUCT_UPSERT = {"product/created", "product/updated"}
_PRODUCT_DELETE = {"product/deleted"}
_ORDER_EVENTS = {"order/created", "order/paid", "order/cancelled", "order/fulfilled"}


def get_secret(env: Optional[dict[str, str]] = None) -> Optional[str]:
    """The app client secret used to sign webhooks, or None when not configured."""
    env = env if env is not None else os.environ
    return (env.get("TN_CLIENT_SECRET") or "").strip() or None


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """True when `signature` is the HMAC-SHA256 hex digest of `raw_body` under `secret`.

    Constant-time compare (hmac.compare_digest) so a wrong signature can't be
    recovered byte-by-byte via timing.
    """
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def _parse(raw_body: bytes) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(raw_body or b"")
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def process(
    raw_body: bytes,
    signature: str,
    *,
    secret: Optional[str] = None,
    client: Optional[TiendaNubeClient] = None,
) -> tuple[dict[str, Any], int]:
    """Verify, parse and dispatch a webhook. Returns ``(json_body, http_status)``.

    Statuses: 200 handled/ignored, 202 accepted-but-deferred (event we act on but
    have no API token to fetch the resource), 400 unparseable body, 401 bad/missing
    signature, 503 not configured (no client secret to verify against).

    The route just serializes the dict and returns the status — no exceptions cross
    this boundary.
    """
    secret = secret if secret is not None else get_secret()
    if not secret:
        return {"ok": False, "reason": "not_configured"}, 503
    if not verify_signature(raw_body, signature, secret):
        return {"ok": False, "reason": "bad_signature"}, 401

    data = _parse(raw_body)
    if data is None:
        return {"ok": False, "reason": "bad_body"}, 400

    event = str(data.get("event") or "")
    resource_id = data.get("id")
    if not event or resource_id is None:
        return {"ok": False, "reason": "bad_body"}, 400

    if event in _PRODUCT_DELETE:
        deleted = catalog_sync.delete_product(int(resource_id))
        return {"ok": True, "event": event, "action": "deleted", "found": deleted}, 200

    if event in _PRODUCT_UPSERT:
        return _handle_product_upsert(event, int(resource_id), client)

    if event in _ORDER_EVENTS:
        # Orders drive backend reconciliation, not the buyer's page — acknowledge so
        # Tienda Nube stops retrying; the /gracias page is reached via the redirect.
        return {"ok": True, "event": event, "action": "acknowledged", "order_id": int(resource_id)}, 200

    return {"ok": True, "event": event, "action": "ignored"}, 200


def _handle_product_upsert(
    event: str, product_id: int, client: Optional[TiendaNubeClient]
) -> tuple[dict[str, Any], int]:
    """Fetch the changed product from the API and upsert it into the mirror.

    Needs a configured client. Without a token we can't fetch, so we accept the
    webhook (202) rather than fail — a later full resync will reconcile.
    """
    if client is None:
        try:
            client = TiendaNubeClient.from_env()
        except ValueError:
            return {"ok": False, "reason": "no_client", "event": event}, 202
    try:
        payload = client.get_product(product_id)
    except TiendaNubeError as exc:
        # A 404 means it's gone from the store — mirror the deletion; other errors
        # are transient (let Tienda Nube retry).
        if exc.status_code == 404:
            catalog_sync.delete_product(product_id)
            return {"ok": True, "event": event, "action": "deleted", "found": True}, 200
        return {"ok": False, "reason": "api_error", "status": exc.status_code}, 502
    row = catalog_sync.upsert_product(payload)
    return {"ok": True, "event": event, "action": "upserted", "tn_id": row.tn_id}, 200
