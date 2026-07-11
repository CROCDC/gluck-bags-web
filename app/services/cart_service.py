"""Server-side shopping cart (headless POC, Fase 3a).

The cart lives in the signed Flask session as a simple ``{product_id: qty}`` map;
`build()` turns it into rich, display-ready line items by looking each product up
through `ProductRepository`. Products are the source of pricing, so a line whose
product was unpublished, deleted or had its price cleared is silently dropped when
the cart is rebuilt — the cart can never show a stale or unpurchasable item.

Only **purchasable** products (published + with a price) can be added; the rest keep
the "Consultar por Instagram" flow. When the Tienda Nube checkout handoff lands
(Fase 3b), `build()` stays the same and only the checkout endpoint changes: it will
create the TN cart from these line items and redirect.

Every function runs inside a Flask request context (it reads `session`).
"""

from __future__ import annotations

from typing import Any

from flask import session, url_for

from app.services import catalog

CART_SESSION_KEY = "cart"
# Per-line cap: a sane upper bound so a crafted request can't set an absurd qty.
MAX_QTY = 20


def format_ars(amount: int) -> str:
    """Whole ARS pesos as '$ 45.000' (es-AR thousands), matching Product.formatted_price."""
    return "$ " + f"{amount:,.0f}".replace(",", ".")


# --- raw session state -------------------------------------------------------


def _raw() -> dict[str, int]:
    cart = session.get(CART_SESSION_KEY)
    return {str(k): int(v) for k, v in cart.items()} if isinstance(cart, dict) else {}


def _save(raw: dict[str, int]) -> None:
    session[CART_SESSION_KEY] = raw
    session.modified = True


def _clamp_qty(qty: Any) -> int:
    """Coerce to an int in [0, MAX_QTY]; anything unparseable becomes 0."""
    try:
        return max(0, min(int(qty), MAX_QTY))
    except (TypeError, ValueError):
        return 0


# --- mutations ---------------------------------------------------------------


def add(product_id: int, qty: int = 1) -> None:
    """Add `qty` of a product (accumulates with what's already in the cart)."""
    pid = str(int(product_id))
    raw = _raw()
    total = _clamp_qty(raw.get(pid, 0) + _clamp_qty(qty))
    if total <= 0:
        raw.pop(pid, None)
    else:
        raw[pid] = total
    _save(raw)


def set_qty(product_id: int, qty: int) -> None:
    """Set the absolute quantity for a product (0 removes it)."""
    pid = str(int(product_id))
    raw = _raw()
    clamped = _clamp_qty(qty)
    if clamped <= 0:
        raw.pop(pid, None)
    else:
        raw[pid] = clamped
    _save(raw)


def remove(product_id: int) -> None:
    raw = _raw()
    raw.pop(str(int(product_id)), None)
    _save(raw)


def clear() -> None:
    _save({})


# --- reads -------------------------------------------------------------------


def count() -> int:
    """Total item quantity, straight from the session (no DB) — for the header badge."""
    return sum(_raw().values())


def is_purchasable(product: Any) -> bool:
    """A product can be added only if it's published, priced and (where the source
    tracks it) in stock. Delegates to the catalogue facade so the rule is identical
    whether the storefront is served from the admin catalogue or the Tienda Nube
    mirror."""
    return catalog.is_purchasable(product)


def build() -> dict[str, Any]:
    """Materialize the cart into display-ready line items, pruning invalid entries.

    Returns a JSON-serializable dict: items (with formatted prices + a thumbnail),
    the item count, and the subtotal. Shipping/taxes are intentionally absent — they
    are computed by Tienda Nube at checkout.
    """
    raw = _raw()
    items: list[dict[str, Any]] = []
    subtotal = 0
    changed = False

    for pid, qty in list(raw.items()):
        product = catalog.get_by_id(int(pid))
        if not is_purchasable(product) or qty <= 0:
            raw.pop(pid, None)
            changed = True
            continue
        line_total = product.price * qty
        subtotal += line_total
        cover = product.cover
        items.append(
            {
                "id": product.id,
                "title": product.title,
                "url": url_for("product_detail", product_id=product.id),
                "category": product.category,
                "price": product.price,
                "price_formatted": product.formatted_price,
                "qty": qty,
                "line_total": line_total,
                "line_total_formatted": format_ars(line_total),
                "image": cover.thumb_url if cover else None,
            }
        )

    if changed:
        _save(raw)

    return {
        "items": items,
        "count": sum(item["qty"] for item in items),
        "subtotal": subtotal,
        "subtotal_formatted": format_ars(subtotal),
        "currency": "ARS",
    }
