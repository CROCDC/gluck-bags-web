"""Checkout handoff to Tienda Nube (headless POC, Fase 3b).

Turns our session cart into a Tienda Nube cart and returns the redirect URL where
the buyer finishes the purchase (payment, shipping, AFIP invoicing — all TN's job).
This is the piece behind ``POST /checkout``.

Wiring status: fully built and tested with mocks, but **inert until two things
exist**: (1) a TN access token in the environment, and (2) cart line items that map
to TN variants. The mapping is the `variant_resolver` seam below. In the POC's
destination — the storefront served from the `TiendaNubeProduct` mirror — cart items
carry TN product ids, so `mirror_variant_resolver` resolves them directly. Until that
swap, `start_checkout` reports a clear, honest status instead of guessing.

Design: every function returns a plain JSON-ready dict, so the route just serializes
it and picks an HTTP status. No exceptions cross the boundary for expected states
(empty cart, unmapped items, no token) — those are data, not errors.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.models import TiendaNubeProduct
from app.services import cart_service
from app.services.tiendanube_client import TiendaNubeClient, TiendaNubeError

# A resolver maps one built cart line -> {"variant_id", "quantity"} or None if the
# product isn't linked to a Tienda Nube variant yet.
VariantResolver = Callable[[dict[str, Any]], Optional[dict[str, Any]]]

_PENDING_MESSAGE = (
    "Estamos conectando el pago con Tienda Nube. Mientras tanto, escribinos y "
    "coordinamos tu compra."
)


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
    client: Optional[TiendaNubeClient] = None,
    resolver: VariantResolver = mirror_variant_resolver,
) -> dict[str, Any]:
    """Attempt the Tienda Nube checkout handoff for the current session cart.

    Returns a dict with `ready` and a `reason`:
      - empty                -> cart has no items
      - integration_pending  -> no TN credentials configured yet
      - unmapped             -> some items aren't linked to a TN variant
      - no_url               -> TN accepted the cart but returned no redirect URL
      - api_error            -> TN returned an error
      - ready=True + redirect_url -> success; the frontend redirects there
    """
    cart = cart_service.build()
    if not cart["items"]:
        return {"ready": False, "reason": "empty", "cart": cart}

    if client is None:
        client = build_client_from_env()
    if client is None:
        return {
            "ready": False,
            "reason": "integration_pending",
            "message": _PENDING_MESSAGE,
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
        checkout = client.create_checkout(line_items)
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
