"""Server-side shopping cart.

The cart lives in the signed Flask session as a simple ``{product_id: qty}`` map;
`build()` turns it into rich, display-ready line items by looking each product up
through the `catalog` facade (in prod, the Tienda Nube mirror — ids are TN product
ids). Products are the source of pricing, so a line whose product was unpublished,
deleted or had its price cleared is silently dropped when the cart is rebuilt — the
cart can never show a stale or unpurchasable item.

Only **purchasable** products (published + priced + in stock) can be added; the
rest fall back to the PDP's "Consultar por Instagram" secondary channel. The
checkout endpoint turns these line items into a TN draft order and redirects to its
hosted checkout (see checkout_service).

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


def _reconcile_pending_checkout() -> None:
    """Settle a completed TN handoff before reading or mutating the cart, so a
    buyer who never returned to /gracias can't re-buy (or keep seeing) lines they
    already purchased. Lazy import: checkout_service pulls the TN client chain,
    which must never load at boot (see the factory's TN guard)."""
    from app.services import checkout_service

    checkout_service.reconcile_pending()


# --- mutations ---------------------------------------------------------------


def add(product_id: int, qty: int = 1) -> None:
    """Add `qty` of a product (accumulates with what's already in the cart)."""
    _reconcile_pending_checkout()
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
    _reconcile_pending_checkout()
    pid = str(int(product_id))
    raw = _raw()
    clamped = _clamp_qty(qty)
    if clamped <= 0:
        raw.pop(pid, None)
    else:
        raw[pid] = clamped
    _save(raw)


def remove(product_id: int) -> None:
    _reconcile_pending_checkout()
    raw = _raw()
    raw.pop(str(int(product_id)), None)
    _save(raw)


def remove_quantities(items: dict[str, int]) -> None:
    """Subtract quantities (the lines of a completed TN checkout) without touching
    lines added afterwards. No reconcile hook: this IS the reconciliation write."""
    raw = _raw()
    for pid, qty in items.items():
        key = str(pid)
        left = raw.get(key, 0) - _clamp_qty(qty)
        if left > 0:
            raw[key] = left
        else:
            raw.pop(key, None)
    _save(raw)


def clear() -> None:
    _save({})


# --- reads -------------------------------------------------------------------


def count() -> int:
    """Total item quantity, straight from the session (no DB, no reconcile) — the
    header badge renders on every page and must stay free of I/O; a stale badge
    settles on the next cart interaction."""
    return sum(_raw().values())


def raw_items() -> dict[str, int]:
    """A copy of the raw {product_id: qty} map (the checkout handoff snapshot)."""
    return dict(_raw())


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
    _reconcile_pending_checkout()
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
