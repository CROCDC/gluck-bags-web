"""Local mirror of the Tienda Nube catalogue (headless POC, Fase 2).

Tienda Nube is the source of truth for the commercial data (variants, price,
stock); this table is a **cache** we refresh from the API (see
app/services/catalog_sync.py) so our own UI can render fast without hitting the API
on every request. It is deliberately a SEPARATE table from `products` (the admin-
managed catalogue): a brand-new table is created by `db.create_all()` with no
migration, and the two data sources stay cleanly separated during the POC.

Names/descriptions come from Tienda Nube localized as {"es": ..., "pt": ...}; we
store the Spanish value (the store's language) plus the full raw payload for
debugging and for fields we haven't promoted to columns yet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.factory import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def localized(value: Any, lang: str = "es") -> str:
    """Flatten a Tienda Nube localized field to a plain string.

    TN returns `{"es": "Bolso", "pt": "Bolsa"}`; pick `lang`, else the first value,
    else "". Plain strings/None pass through so callers don't have to type-check.
    """
    if isinstance(value, dict):
        if not value:
            return ""
        return str(value.get(lang) or next(iter(value.values()), ""))
    return "" if value is None else str(value)


def _variant_stock(variant: dict[str, Any]) -> int | None:
    """A variant's stock, or None when Tienda Nube reports it as unmanaged/unlimited
    (stock is null when `stock_management` is off)."""
    stock = variant.get("stock")
    return None if stock is None else int(stock)


class TiendaNubeProduct(db.Model):
    """One product mirrored from the Tienda Nube API, keyed by its TN id."""

    __tablename__ = "tiendanube_products"

    id = db.Column(db.Integer, primary_key=True)
    # The product id in Tienda Nube — the natural key we upsert on.
    tn_id = db.Column(db.Integer, nullable=False, unique=True, index=True)

    handle = db.Column(db.String(255), nullable=True)  # URL slug in the TN store
    name = db.Column(db.String(255), nullable=False, default="")
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(120), nullable=True)  # first category's name
    published = db.Column(db.Boolean, nullable=False, default=False)
    # Absolute URL of the product in the TN store — a safe fallback destination for
    # the checkout redirect and a canonical reference.
    canonical_url = db.Column(db.String(500), nullable=True)

    # Commercial summary, derived from variants for convenient rendering/sorting.
    # Price is kept as the raw TN string (e.g. "45000.00") to avoid rounding.
    price = db.Column(db.String(32), nullable=True)  # lowest variant price
    currency = db.Column(db.String(8), nullable=True)
    # Total stock across variants. None means at least one variant is unmanaged
    # (effectively unlimited) — never treat None as "out of stock".
    stock = db.Column(db.Integer, nullable=True)

    # Full fidelity for the fields we don't (yet) promote to columns.
    variants = db.Column(db.JSON, nullable=True)  # [{id, sku, price, stock, values}]
    images = db.Column(db.JSON, nullable=True)  # ["https://...", ...]
    raw = db.Column(db.JSON, nullable=True)  # the untouched API payload

    synced_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    # --- mapping -------------------------------------------------------------

    def apply_payload(self, payload: dict[str, Any], *, lang: str = "es") -> "TiendaNubeProduct":
        """Populate this row from a Tienda Nube product payload (idempotent)."""
        self.tn_id = int(payload["id"])
        self.handle = localized(payload.get("handle"), lang) or None
        self.name = localized(payload.get("name"), lang)
        self.description = localized(payload.get("description"), lang) or None
        self.published = bool(payload.get("published"))
        self.canonical_url = payload.get("canonical_url") or None

        categories = payload.get("categories") or []
        self.category = localized(categories[0].get("name"), lang) if categories else None

        variants = payload.get("variants") or []
        self.variants = variants
        self.price, self.currency = _lowest_price(variants)
        self.stock = _total_stock(variants)

        self.images = [img.get("src") for img in (payload.get("images") or []) if img.get("src")]
        self.raw = payload
        self.synced_at = _utcnow()
        return self

    @property
    def formatted_price(self) -> str | None:
        """Price as '$ 45.000' (es-AR thousands), or None when unset."""
        if not self.price:
            return None
        try:
            amount = float(self.price)
        except (TypeError, ValueError):
            return None
        return "$ " + f"{amount:,.0f}".replace(",", ".")

    @property
    def in_stock(self) -> bool:
        """True when purchasable. None stock = unmanaged/unlimited = in stock."""
        return self.stock is None or self.stock > 0

    def __repr__(self) -> str:
        return f"<TiendaNubeProduct tn_id={self.tn_id} {self.name!r}>"


# --- payload helpers ---------------------------------------------------------


def _lowest_price(variants: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Lowest variant price (as the raw TN string) and its currency, or (None, None).

    We show the cheapest variant as the "from" price, matching how storefronts
    present a product with multiple variants.
    """
    priced = [v for v in variants if v.get("price") not in (None, "")]
    if not priced:
        return None, None
    cheapest = min(priced, key=lambda v: _to_float(v.get("price")))
    return str(cheapest.get("price")), cheapest.get("currency") or None


def _total_stock(variants: list[dict[str, Any]]) -> int | None:
    """Sum of managed variant stock, or None if any variant is unmanaged (unlimited)."""
    total = 0
    for variant in variants:
        stock = _variant_stock(variant)
        if stock is None:
            return None
        total += stock
    return total


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")
