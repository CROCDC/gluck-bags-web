"""Persistence rules for the site-text overrides.

The table stores overrides ONLY, and the publish/discard transitions decide when a
row stops existing. Getting that wrong leaks phantom "edited" flags forever, so the
lifecycle is pinned here.
"""

from __future__ import annotations

import pytest

from app.content import registry
from app.models import SiteText
from app.repositories import SiteTextRepository as Repo

KEY = "home.hero.subtitle"
OTHER = "nav.cta"
DEFAULTS = registry.DEFAULTS


@pytest.fixture
def ctx(app):
    with app.test_request_context("/"):
        yield app


def _row(key: str) -> SiteText | None:
    return Repo.get(key)


# --- drafts --------------------------------------------------------------------


def test_setting_a_draft_creates_the_row(ctx) -> None:
    Repo.set_draft(KEY, "Borrador")
    Repo.save()
    row = _row(KEY)
    assert row is not None
    assert row.draft_value == "Borrador"
    assert row.published_value is None
    assert row.has_draft is True


def test_clearing_the_only_draft_removes_the_row(ctx) -> None:
    """No override and no draft means no row — that is what keeps 'edited' honest."""
    Repo.set_draft(KEY, "Borrador")
    Repo.save()
    Repo.set_draft(KEY, None)
    Repo.save()
    assert _row(KEY) is None


def test_clearing_a_draft_keeps_a_published_override(ctx) -> None:
    Repo.set_published(KEY, "Publicado")
    Repo.set_draft(KEY, "Borrador")
    Repo.save()
    Repo.set_draft(KEY, None)
    Repo.save()
    row = _row(KEY)
    assert row is not None and row.published_value == "Publicado" and row.draft_value is None


def test_clearing_a_draft_that_was_never_set_is_a_no_op(ctx) -> None:
    assert Repo.set_draft(KEY, None) is None
    Repo.save()
    assert _row(KEY) is None


# --- publishing ----------------------------------------------------------------


def test_publishing_promotes_the_draft(ctx) -> None:
    Repo.set_draft(KEY, "Nuevo")
    Repo.save()
    assert Repo.publish([KEY], DEFAULTS) == 1
    Repo.save()
    row = _row(KEY)
    assert row is not None and row.published_value == "Nuevo" and row.draft_value is None


def test_publishing_a_value_equal_to_the_default_clears_the_override(ctx) -> None:
    """Restoring the original takes the key back to "not overridden" — but the row
    survives to remember the wording that was live, which is what makes "Volver al
    texto anterior" possible."""
    Repo.set_published(KEY, "Algo editado")
    Repo.set_draft(KEY, DEFAULTS[KEY])
    Repo.save()
    assert Repo.publish([KEY], DEFAULTS) == 1
    Repo.save()
    row = _row(KEY)
    assert row.published_value is None and row.draft_value is None
    assert row.previous_value == "Algo editado"


def test_a_row_with_nothing_left_to_remember_is_deleted(ctx) -> None:
    Repo.set_draft(KEY, "Borrador")
    Repo.save()
    Repo.set_draft(KEY, None)
    Repo.save()
    assert _row(KEY) is None


def test_publishing_records_the_wording_it_replaced(ctx) -> None:
    Repo.set_draft(KEY, "Primera")
    Repo.save()
    Repo.publish([KEY], DEFAULTS)
    Repo.save()
    Repo.set_draft(KEY, "Segunda")
    Repo.save()
    Repo.publish([KEY], DEFAULTS)
    Repo.save()
    row = _row(KEY)
    assert row.published_value == "Segunda" and row.previous_value == "Primera"


def test_draft_keys_lists_what_a_publish_would_touch(ctx) -> None:
    assert Repo.draft_keys() == []
    Repo.set_draft(KEY, "A")
    Repo.set_published(OTHER, "ya publicado")
    Repo.save()
    assert Repo.draft_keys() == [KEY]


def test_publishing_ignores_keys_without_a_draft(ctx) -> None:
    Repo.set_published(KEY, "Publicado")
    Repo.save()
    assert Repo.publish([KEY, OTHER], DEFAULTS) == 0
    Repo.save()
    assert _row(KEY).published_value == "Publicado"


def test_publishing_only_touches_the_requested_keys(ctx) -> None:
    Repo.set_draft(KEY, "A")
    Repo.set_draft(OTHER, "B")
    Repo.save()
    assert Repo.publish([KEY], DEFAULTS) == 1
    Repo.save()
    assert _row(KEY).published_value == "A"
    assert _row(OTHER).published_value is None
    assert _row(OTHER).draft_value == "B"


def test_publishing_an_empty_key_list_is_a_no_op(ctx) -> None:
    Repo.set_draft(KEY, "A")
    Repo.save()
    assert Repo.publish([], DEFAULTS) == 0


# --- discarding ----------------------------------------------------------------


def test_discarding_drops_the_draft_and_keeps_what_is_live(ctx) -> None:
    Repo.set_published(KEY, "Publicado")
    Repo.set_draft(KEY, "Borrador")
    Repo.save()
    assert Repo.discard_drafts([KEY]) == 1
    Repo.save()
    row = _row(KEY)
    assert row is not None and row.published_value == "Publicado" and row.draft_value is None


def test_discarding_a_draft_of_a_never_published_key_removes_the_row(ctx) -> None:
    Repo.set_draft(KEY, "Borrador")
    Repo.save()
    assert Repo.discard_drafts([KEY]) == 1
    Repo.save()
    assert _row(KEY) is None


def test_discarding_counts_only_real_drafts(ctx) -> None:
    Repo.set_published(KEY, "Publicado")
    Repo.save()
    assert Repo.discard_drafts([KEY, OTHER]) == 0


# --- reads ---------------------------------------------------------------------


def test_as_map_returns_every_override_in_one_shot(ctx) -> None:
    Repo.set_published(KEY, "P")
    Repo.set_draft(OTHER, "D")
    Repo.save()
    assert Repo.as_map() == {KEY: ("P", None), OTHER: (None, "D")}


def test_get_all_is_sorted_by_key(ctx) -> None:
    for key in (OTHER, KEY):
        Repo.set_published(key, "x")
    Repo.save()
    assert [row.key for row in Repo.get_all()] == sorted([KEY, OTHER])


def test_delete_reports_whether_there_was_anything_to_delete(ctx) -> None:
    assert Repo.delete(KEY) is False
    Repo.set_published(KEY, "x")
    Repo.save()
    assert Repo.delete(KEY) is True
    Repo.save()
    assert _row(KEY) is None


def test_rollback_discards_staged_changes(ctx) -> None:
    Repo.set_published(KEY, "x")
    Repo.rollback()
    assert _row(KEY) is None
