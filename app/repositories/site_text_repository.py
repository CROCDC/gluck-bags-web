from __future__ import annotations

from app.factory import db
from app.models import SiteText


class SiteTextRepository:
    """Database access for the site-text overrides. Routes/services call these."""

    @staticmethod
    def get_all() -> list[SiteText]:
        return SiteText.query.order_by(SiteText.key.asc()).all()

    @staticmethod
    def get(key: str) -> SiteText | None:
        return db.session.get(SiteText, key)

    @staticmethod
    def draft_keys() -> list[str]:
        """Keys with a pending draft — what a Publicar would put live."""
        return [row.key for row in SiteText.query.filter(SiteText.draft_value.isnot(None)).all()]

    @staticmethod
    def as_map() -> dict[str, tuple[str | None, str | None]]:
        """`key -> (published_value, draft_value)` for every override, in one query.

        The resolver loads this once per request instead of caching it in the
        process: with several gunicorn workers on one SQLite file there is no
        cross-process invalidation, so a process-level cache would serve stale copy
        after an edit. The table is one small row per overridden string.
        """
        return {row.key: (row.published_value, row.draft_value) for row in SiteText.query.all()}

    @staticmethod
    def set_draft(key: str, value: str | None) -> SiteText | None:
        """Stage `value` as the pending edit for `key` (None clears the draft).

        Returns the row, or None when clearing the draft left nothing to store.
        """
        row = db.session.get(SiteText, key)
        if row is None:
            if value is None:
                return None
            row = SiteText(key=key)
            db.session.add(row)
        row.draft_value = value
        if row.is_empty:
            db.session.delete(row)
            return None
        return row

    @staticmethod
    def set_published(key: str, value: str | None) -> SiteText | None:
        """Write the live value for `key` directly (None = back to the default)."""
        row = db.session.get(SiteText, key)
        if row is None:
            if value is None:
                return None
            row = SiteText(key=key)
            db.session.add(row)
        row.published_value = value
        if row.is_empty:
            db.session.delete(row)
            return None
        return row

    @staticmethod
    def publish(keys: list[str], defaults: dict[str, str]) -> int:
        """Promote pending drafts to live. Returns how many keys changed.

        A draft equal to the registry default collapses back to "no override" (the
        row is deleted), so restoring the original copy leaves no residue behind.
        """
        changed = 0
        rows = SiteText.query.filter(SiteText.key.in_(keys)).all() if keys else []
        for row in rows:
            if row.draft_value is None:
                continue
            value: str | None = row.draft_value
            if value == defaults.get(row.key):
                value = None
            # Remember what the shop had live before this. Publishing used to be a
            # one-way door: the previous wording existed nowhere afterwards.
            row.previous_value = row.published_value
            row.published_value = value
            row.draft_value = None
            changed += 1
            if row.is_empty:
                db.session.delete(row)
        return changed

    @staticmethod
    def revert(key: str) -> bool:
        """Swap the live value with the one it replaced.

        `set_published(key, previous)` overwrote `published_value` and left
        `previous_value` alone, so the wording being reverted AWAY from was destroyed
        and a second revert had nowhere to go — a mis-click was as unrecoverable as
        the mistake this exists to undo.
        """
        row = db.session.get(SiteText, key)
        if row is None or row.previous_value is None:
            return False
        row.published_value, row.previous_value = row.previous_value, row.published_value
        return True

    @staticmethod
    def discard_drafts(keys: list[str]) -> int:
        """Drop the pending edits for `keys`. Returns how many were dropped."""
        dropped = 0
        rows = SiteText.query.filter(SiteText.key.in_(keys)).all() if keys else []
        for row in rows:
            if row.draft_value is None:
                continue
            row.draft_value = None
            dropped += 1
            if row.is_empty:
                db.session.delete(row)
        return dropped

    @staticmethod
    def delete(key: str) -> bool:
        row = db.session.get(SiteText, key)
        if row is None:
            return False
        db.session.delete(row)
        return True

    @staticmethod
    def save() -> None:
        db.session.commit()
        SiteTextRepository._invalidate_resolver_cache()

    @staticmethod
    def rollback() -> None:
        """Drop everything staged in this request (a rejected form submission)."""
        db.session.rollback()
        SiteTextRepository._invalidate_resolver_cache()

    @staticmethod
    def _invalidate_resolver_cache() -> None:
        """The resolver snapshots the overrides once per request; a write makes that
        snapshot stale, so drop it here rather than expecting callers to remember."""
        from app.content import resolver

        resolver.invalidate()
