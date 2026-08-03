from __future__ import annotations

from datetime import datetime, timezone

from app.factory import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SiteText(db.Model):
    """One editor override of a registry text field.

    The table holds ONLY overrides: a key with no row renders its registry default
    (see app/content/registry.py). That keeps a fresh deploy identical to the code,
    makes "restore the original" a plain DELETE, and means new copy never needs a
    data migration.

    Two values per key so the editor can work without touching the live site:
    `published_value` is what the public renders, `draft_value` is the pending edit
    the preview renders. `None` in either means "fall through" — an unset draft is
    no pending change; an unset published value is the registry default.
    """

    __tablename__ = "site_texts"

    key = db.Column(db.String(120), primary_key=True)
    published_value = db.Column(db.Text, nullable=True)
    draft_value = db.Column(db.Text, nullable=True)
    # What was live before the last publish. Publishing used to overwrite the live
    # copy with nowhere to go back to: "Restaurar original" returns the factory text,
    # not the wording the shop had yesterday.
    previous_value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    @property
    def has_draft(self) -> bool:
        return self.draft_value is not None

    @property
    def is_empty(self) -> bool:
        """True when the row carries no information and can be deleted."""
        return (
            self.published_value is None
            and self.draft_value is None
            and self.previous_value is None
        )

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"<SiteText {self.key!r} published={self.published_value is not None} draft={self.has_draft}>"
