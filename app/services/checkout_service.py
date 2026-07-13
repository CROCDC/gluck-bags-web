"""Checkout handoff to Tienda Nube.

Turns our session cart into a Tienda Nube draft order and returns the redirect URL
where the buyer finishes the purchase (payment, shipping, AFIP invoicing — all TN's
job). This is the piece behind ``POST /checkout``.

The buyer's email is captured in our cart UI and sent as the draft order's contact
(TN hard-requires a non-blank contact_email and prefills the hosted checkout with
it), so order confirmation/tracking emails reach the real buyer.

TN's checkout has no configurable return URL, so the buyer usually ends on TN's own
thank-you page instead of /gracias. `remember_pending` + `reconcile_pending` close
that gap: the draft-order id plus a snapshot of the handed-off lines are kept in
the session and, on a later cart use, the draft order is fetched once (short
timeout, throttled, TTL-bounded) to detect completion and drop exactly the
purchased lines from the cart.

Design: every function returns a plain JSON-ready dict, so the route just serializes
it and picks an HTTP status. No exceptions cross the boundary for expected states
(empty cart, unmapped items, no token) — those are data, not errors.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Optional

import requests
from flask import session

from app.models import TiendaNubeProduct
from app.services import cart_service
from app.services.tiendanube_client import TiendaNubeClient, TiendaNubeError

logger = logging.getLogger(__name__)

# A resolver maps one built cart line -> {"variant_id", "quantity"} or None if the
# product isn't linked to a Tienda Nube variant yet.
VariantResolver = Callable[[dict[str, Any]], Optional[dict[str, Any]]]

# In prod a missing TN client means a config regression (lost credentials), not a
# planned state — tell the buyer it's transient and keep the purchase flow as the
# only story; support stays a separate channel.
_UNCONFIGURED_MESSAGE = "No pudimos iniciar el pago. Probá de nuevo en unos minutos."

_EMAIL_MESSAGE = "Ingresá tu email para recibir la confirmación del pedido."

# Deliverability is TN's problem; this only rejects obvious junk ("a@", "x@y")
# before it becomes an opaque api_error for the buyer.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def mirror_variant_resolver(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Resolve a cart line to a TN variant via the local mirror.

    Assumes the cart line's ``id`` is a Tienda Nube product id (true once the
    storefront is served from `TiendaNubeProduct`). Uses the product's first
    variant — GLÜCK's catalogue is single-variant per product today; multi-variant
    selection (size/colour) is a later refinement.
    """
    row = TiendaNubeProduct.query.filter_by(tn_id=int(item["id"])).one_or_none()
    variants = (row.variants if row else None) or []
    if not variants:
        return None
    variant_id = variants[0].get("id")
    if variant_id is None:
        return None
    return {"variant_id": int(variant_id), "quantity": int(item["qty"])}


def build_client_from_env() -> Optional[TiendaNubeClient]:
    """A configured client, or None when credentials aren't set (POC not yet live)."""
    try:
        return TiendaNubeClient.from_env()
    except ValueError:
        return None


def start_checkout(
    *,
    contact_email: str | None = None,
    client: Optional[TiendaNubeClient] = None,
    resolver: VariantResolver = mirror_variant_resolver,
) -> dict[str, Any]:
    """Attempt the Tienda Nube checkout handoff for the current session cart.

    Returns a dict with `ready` and a `reason`:
      - empty          -> cart has no items
      - email_required -> no valid buyer email was provided
      - not_configured -> TN credentials missing (an ops regression in prod)
      - unmapped       -> some items aren't linked to a TN variant
      - no_url         -> TN accepted the cart but returned no redirect URL
      - api_error      -> TN returned an error
      - ready=True + redirect_url -> success; the frontend redirects there
    """
    cart = cart_service.build()
    if not cart["items"]:
        return {"ready": False, "reason": "empty", "cart": cart}

    email = str(contact_email or "").strip()
    if not _EMAIL_RE.fullmatch(email):
        return {
            "ready": False,
            "reason": "email_required",
            "message": _EMAIL_MESSAGE,
            "cart": cart,
        }

    if client is None:
        client = build_client_from_env()
    if client is None:
        logger.error("checkout attempted without Tienda Nube credentials configured")
        return {
            "ready": False,
            "reason": "not_configured",
            "message": _UNCONFIGURED_MESSAGE,
            "cart": cart,
        }

    line_items: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for item in cart["items"]:
        resolved = resolver(item)
        if resolved:
            line_items.append(resolved)
        else:
            unmapped.append(item["title"])
    if unmapped:
        return {
            "ready": False,
            "reason": "unmapped",
            "unmapped": unmapped,
            "message": "Algunos productos todavía no están vinculados a Tienda Nube.",
            "cart": cart,
        }

    try:
        checkout = client.create_checkout(line_items, contact={"contact_email": email})
    except TiendaNubeError as exc:
        return {
            "ready": False,
            "reason": "api_error",
            "status": exc.status_code,
            "message": "No pudimos iniciar el checkout en Tienda Nube.",
            "cart": cart,
        }

    url = checkout.get("checkout_url")
    if not url:
        return {"ready": False, "reason": "no_url", "cart": cart}

    return {"ready": True, "redirect_url": url, "checkout_id": checkout.get("id")}


# --- post-purchase reconciliation ---------------------------------------------

PENDING_SESSION_KEY = "tn_checkout"
# Don't poll TN before the buyer could plausibly have paid, at most once per
# interval afterwards, and give up after the TTL — a bounded, short-timeout probe
# per session per window, never an unbounded hot-path dependency on TN.
_RECONCILE_MIN_AGE = 120
_RECONCILE_INTERVAL = 300
_PENDING_TTL = 60 * 60 * 48


def _save_pending(pending: Optional[dict[str, Any]]) -> None:
    if pending is None:
        session.pop(PENDING_SESSION_KEY, None)
    else:
        session[PENDING_SESSION_KEY] = pending


def remember_pending(checkout_id: Any, items: Optional[dict[str, int]] = None) -> None:
    """Track the draft order handed off for this session (TN has no return URL
    back to /gracias), plus a snapshot of the handed-off lines so completion can
    drop exactly what was bought — never items added afterwards."""
    if checkout_id is None:
        return
    now = int(time.time())
    _save_pending({"id": checkout_id, "ts": now, "checked": 0, "items": items or {}})


def forget_pending() -> None:
    _save_pending(None)


def has_pending() -> bool:
    return isinstance(session.get(PENDING_SESSION_KEY), dict)


def consume_pending_purchase() -> None:
    """Drop the handed-off lines from the cart (the exact snapshot when available,
    the whole cart otherwise) and stop tracking the handoff."""
    pending = session.get(PENDING_SESSION_KEY)
    items = pending.get("items") if isinstance(pending, dict) else None
    if items:
        cart_service.remove_quantities(items)
    else:
        cart_service.clear()
    forget_pending()


def _reconcile_client() -> Optional[TiendaNubeClient]:
    # Interactive path: a probe inside a page render must fail fast, not inherit
    # the batch-sync timeout/retry budget.
    try:
        return TiendaNubeClient.from_env(timeout=3, max_retries=0)
    except ValueError:
        return None


def _draft_completed(draft: dict[str, Any]) -> bool:
    return bool(
        draft.get("completed_at")
        or draft.get("paid_at")
        or draft.get("status") == "closed"
    )


def reconcile_pending(*, client: Optional[TiendaNubeClient] = None) -> bool:
    """If this session handed a cart off to TN and the buyer never came back to
    /gracias, check (throttled) whether the draft order completed and drop the
    purchased lines. Returns True when the cart was reconciled."""
    pending = session.get(PENDING_SESSION_KEY)
    if not isinstance(pending, dict) or pending.get("id") is None:
        return False

    now = int(time.time())
    started = int(pending.get("ts", 0))
    if now - started > _PENDING_TTL:
        forget_pending()
        return False
    if now - started < _RECONCILE_MIN_AGE:
        return False
    if now - int(pending.get("checked", 0)) < _RECONCILE_INTERVAL:
        return False

    # Stamp BEFORE the call: a failing TN (timeouts included) can never be probed
    # more than once per interval.
    _save_pending({**pending, "checked": now})

    if client is None:
        client = _reconcile_client()
    if client is None:
        return False

    try:
        draft = client.get_draft_order(pending["id"])
    except (TiendaNubeError, requests.RequestException):
        return False

    if _draft_completed(draft):
        consume_pending_purchase()
        return True
    if draft.get("status") == "cancelled":
        forget_pending()
    return False
