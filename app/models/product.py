from __future__ import annotations

from datetime import datetime, timezone

from app.factory import db
from app.models.media import Media


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Product(db.Model):
    """A bag shown on the landing page and managed from the admin panel."""

    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    # Nullable: the live catalogue has no descriptions (products link to Instagram),
    # so seeded/imported products store NULL. The admin form normalises blanks to "".
    description = db.Column(db.Text, nullable=True)
    # Whole ARS pesos. None means "price not set yet" (shown as "Consultar"). The
    # live catalogue shows no prices, so seeded products are NULL here too.
    price = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(8), nullable=False, default="ARS")
    category = db.Column(db.String(80), nullable=True)
    # Unpublished products are hidden from the public site but kept in the admin.
    is_published = db.Column(db.Boolean, nullable=False, default=True)
    # Manual sort order on the public grid and the admin list (lower = first).
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    media = db.relationship(
        "Media",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="Media.position",
    )

    @property
    def cover(self) -> Media | None:
        """The media flagged as cover, else the first one (None if empty)."""
        for item in self.media:
            if item.is_cover:
                return item
        return self.media[0] if self.media else None

    @property
    def images(self) -> list[Media]:
        return [m for m in self.media if m.is_image]

    @property
    def videos(self) -> list[Media]:
        return [m for m in self.media if m.is_video]

    @property
    def formatted_price(self) -> str | None:
        """Price as '$ 45.000' (es-AR thousands), or None when unset."""
        if self.price is None:
            return None
        return "$ " + f"{self.price:,.0f}".replace(",", ".")

    def __repr__(self) -> str:
        return f"<Product {self.id} {self.title!r}>"
