from __future__ import annotations

from datetime import datetime, timezone

from app.factory import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

# Public URL prefix served by the `media_file` route from MEDIA_ROOT. Uploaded
# files are immutable (a new upload = a new directory), so URLs need no cache
# buster and can be cached aggressively.
MEDIA_URL_PREFIX = "/media"


class Media(db.Model):
    """One photo or video belonging to a product.

    Files live under `MEDIA_ROOT/<path>/`. Images store responsive variants
    `<width>.jpg` / `<width>.webp`; videos store `video.mp4` + `poster.jpg`.
    """

    __tablename__ = "product_media"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    kind = db.Column(db.String(10), nullable=False)  # 'image' | 'video'
    # Directory holding this item's files, relative to MEDIA_ROOT (e.g. 'products/3/7').
    path = db.Column(db.String(255), nullable=False)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    # For images: the responsive widths actually generated (e.g. [400, 600, 1000]).
    widths = db.Column(db.JSON, nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    is_cover = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    product = db.relationship("Product", back_populates="media")

    # --- helpers ---------------------------------------------------------------

    @property
    def is_image(self) -> bool:
        return self.kind == "image"

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    def _url(self, name: str) -> str:
        return f"{MEDIA_URL_PREFIX}/{self.path}/{name}"

    # Images ---
    def image_url(self, width: int | None, ext: str = "jpg") -> str | None:
        if not width:
            return None
        return self._url(f"{width}.{ext}")

    def image_srcset(self, ext: str = "jpg", max_width: int | None = None) -> str:
        ws = self.widths or []
        if max_width is not None:
            ws = [w for w in ws if w <= max_width] or ws[:1]
        return ", ".join(f"{self._url(f'{w}.{ext}')} {w}w" for w in ws)

    @property
    def largest_width(self) -> int | None:
        ws = self.widths or []
        return max(ws) if ws else None

    @property
    def smallest_width(self) -> int | None:
        ws = self.widths or []
        return min(ws) if ws else None

    @property
    def default_image_url(self) -> str | None:
        w = self.largest_width
        return self._url(f"{w}.jpg") if w else None

    @property
    def og_image_url(self) -> str | None:
        """The 1200x630 social-share crop (image/jpeg) when it has been generated,
        else None. Returns None for media processed before the og variant existed,
        so callers can fall back to the regular cover without a 404."""
        import os

        from flask import current_app, has_app_context

        if not self.is_image:
            return None
        rel = f"{self.path}/og.jpg"
        if has_app_context():
            root = current_app.config.get("MEDIA_ROOT")
            if root and os.path.exists(os.path.join(root, rel)):
                return f"{MEDIA_URL_PREFIX}/{rel}"
        return None

    # Videos ---
    @property
    def video_url(self) -> str:
        return self._url("video.mp4")

    @property
    def poster_url(self) -> str:
        return self._url("poster.jpg")

    # Generic ---
    @property
    def thumb_url(self) -> str | None:
        """A small preview used in the admin and as the grid cover image."""
        if self.is_image:
            w = self.smallest_width
            return self._url(f"{w}.webp") if w else self.default_image_url
        return self.poster_url

    def __repr__(self) -> str:
        return f"<Media {self.id} {self.kind} product={self.product_id}>"
