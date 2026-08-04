"""Resolution rules: default -> published -> (preview only) draft, plus tokens.

These are the semantics every page depends on, so they're tested directly against
the resolver rather than through rendered HTML.
"""

from __future__ import annotations

import pytest
from markupsafe import Markup

from app import content
from app.content import registry
from app.repositories import SiteTextRepository

KEY = "home.hero.subtitle"
RICH_KEY = "home.feature.title"


@pytest.fixture
def ctx(app):
    """An app context with a request, which is what the per-request cache needs."""
    with app.test_request_context("/"):
        yield app


def _publish(key: str, value: str | None) -> None:
    SiteTextRepository.set_published(key, value)
    SiteTextRepository.save()


def _draft(key: str, value: str | None) -> None:
    SiteTextRepository.set_draft(key, value)
    SiteTextRepository.save()


# --- precedence ----------------------------------------------------------------


def test_without_an_override_the_registry_default_is_used(ctx) -> None:
    assert content.t(KEY) == registry.FIELDS[KEY].default


def test_a_published_override_replaces_the_default(ctx) -> None:
    _publish(KEY, "Texto nuevo")
    assert content.t(KEY) == "Texto nuevo"


def test_a_draft_is_invisible_outside_preview(ctx) -> None:
    _publish(KEY, "Publicado")
    _draft(KEY, "Borrador")
    assert content.t(KEY) == "Publicado"


def test_a_draft_wins_in_preview_mode(app) -> None:
    with app.test_request_context("/"):
        _publish(KEY, "Publicado")
        _draft(KEY, "Borrador")
    from app import auth

    with app.test_request_context("/?preview=1"):
        auth.login()
        assert content.is_preview() is True
        assert content.t(KEY) == "Borrador"


def test_preview_without_a_session_is_ignored(app) -> None:
    with app.test_request_context("/"):
        _publish(KEY, "Publicado")
        _draft(KEY, "Borrador")
    with app.test_request_context("/?preview=1"):
        assert content.is_preview() is False
        assert content.t(KEY) == "Publicado"


def test_deleting_the_override_restores_the_default(ctx) -> None:
    _publish(KEY, "Texto nuevo")
    assert content.t(KEY) == "Texto nuevo"
    SiteTextRepository.delete(KEY)
    SiteTextRepository.save()
    with ctx.test_request_context("/"):  # a new request => a new cache
        assert content.t(KEY) == registry.FIELDS[KEY].default


# --- tokens --------------------------------------------------------------------


def test_global_tokens_are_interpolated(ctx) -> None:
    _publish(KEY, "Somos {brand} y hacemos {tagline}")
    assert content.t(KEY) == "Somos GLÜCK y hacemos Bolsos minimalistas de cuero vegano"


def test_editing_the_brand_propagates_everywhere(ctx) -> None:
    _publish("global.brand", "MARCA NUEVA")
    _publish(KEY, "Hola {brand}")
    assert content.t(KEY) == "Hola MARCA NUEVA"


def test_per_call_params_are_interpolated(ctx) -> None:
    value = content.t("category.meta.title", category="Tote")
    assert value.startswith("Tote · ")
    assert "{category}" not in value


def test_unknown_tokens_are_left_alone_instead_of_raising(ctx) -> None:
    """An editor typing a stray brace must never 500 a public page."""
    _publish(KEY, "Hola {no_existe} y {brand}")
    assert content.t(KEY) == "Hola {no_existe} y GLÜCK"


def test_a_lone_brace_does_not_raise(ctx) -> None:
    _publish(KEY, "50% { descuento")
    assert content.t(KEY) == "50% { descuento"


def test_the_year_token_resolves(ctx) -> None:
    from datetime import datetime

    assert str(datetime.now().year) in content.t("footer.copyright")


# --- types ---------------------------------------------------------------------


def test_rich_values_come_back_as_sanitized_markup(ctx) -> None:
    _publish(RICH_KEY, "Hola<br>mundo<script>alert(1)</script>")
    value = content.t(RICH_KEY)
    assert isinstance(value, Markup)
    assert "<br>" in value
    assert "script" not in value


def test_plain_values_are_not_markup_so_jinja_escapes_them(ctx) -> None:
    _publish(KEY, "<b>no soy html</b>")
    value = content.t(KEY)
    assert not isinstance(value, Markup)
    assert value == "<b>no soy html</b>"


def test_a_token_value_is_data_not_markup(ctx) -> None:
    """A token is escaped before it is spliced into a rich value.

    The old version of this test used `<img onerror>` — a tag the sanitizer drops
    anyway — so it asserted the sanitizer's behaviour, not the property. An
    ALLOW-LISTED tag is the case that matters: it used to survive, which let the
    brand field put a link into every legal page.
    """
    _publish("global.brand", '<a href="//evil.test">GLUCK</a>')
    _publish(RICH_KEY, "Marca: {brand}")
    value = str(content.t(RICH_KEY))
    # No live element: the tag is escaped, so it reads as text on the page instead of
    # linking anywhere. ("evil.test" IS present — as visible characters.)
    assert "<a " not in value
    assert "&lt;a href=" in value
    assert "GLUCK" in value

    _publish("global.brand", "<img src=x onerror=alert(1)>")
    assert "<img" not in str(content.t(RICH_KEY))


def test_an_edit_marker_never_survives_as_a_parameter(ctx) -> None:
    """Stripping only the three marker characters left the KEY between them, so the
    registry key ended up spliced into the copy the shop shows."""
    from app import auth
    from app.content import resolver

    with ctx.test_request_context("/?edit=1"):
        auth.login()
        tagged = resolver.editable("category.tote.label")
        rendered = str(resolver.editable("category.meta.title", category=tagged))
        assert "category.tote.label" not in rendered
        assert "Tote · Carteras" in resolver._strip_markers(rendered)
        # The edit-mode value, once the markers come off, is what a normal render says.
        assert resolver._strip_markers(rendered) == str(
            resolver.t("category.meta.title", category="Tote")
        )


def test_lines_fields_split_into_a_list(ctx) -> None:
    _publish("home.marquee.phrases", "Uno\n\n  Dos  \nTres\n")
    assert content.t_lines("home.marquee.phrases") == ["Uno", "Dos", "Tres"]


# --- failure modes -------------------------------------------------------------


def test_an_unknown_key_raises_in_tests_so_a_typo_cannot_ship(ctx) -> None:
    with pytest.raises(KeyError):
        content.t("no.such.key")


def test_an_unknown_key_degrades_to_empty_in_production(app) -> None:
    app.testing = False
    app.debug = False
    try:
        with app.test_request_context("/"):
            assert content.t("no.such.key") == ""
    finally:
        app.testing = True


def test_a_database_failure_degrades_to_the_defaults(app, monkeypatch) -> None:
    """Copy must never be able to take the site down."""

    def boom() -> dict:
        raise RuntimeError("db is gone")

    monkeypatch.setattr(SiteTextRepository, "as_map", staticmethod(boom))
    with app.test_request_context("/"):
        assert content.t(KEY) == registry.FIELDS[KEY].default


# --- caching -------------------------------------------------------------------


def test_the_overrides_are_read_once_per_request(app, monkeypatch) -> None:
    calls = {"n": 0}
    original = SiteTextRepository.as_map

    def counting() -> dict:
        calls["n"] += 1
        return original()

    monkeypatch.setattr(SiteTextRepository, "as_map", staticmethod(counting))
    with app.test_request_context("/"):
        for _ in range(25):
            content.t(KEY)
            content.t("nav.cta")
    assert calls["n"] == 1


def test_a_new_request_sees_a_fresh_value(app) -> None:
    """No process-level cache: several workers share one SQLite file, so an edit has
    to be visible to the next request without any invalidation dance."""
    with app.test_request_context("/"):
        assert content.t(KEY) == registry.FIELDS[KEY].default
        _publish(KEY, "Recién editado")
    with app.test_request_context("/"):
        assert content.t(KEY) == "Recién editado"


# --- category helpers ----------------------------------------------------------


def test_category_label_is_editable_but_the_slug_is_not(ctx) -> None:
    _publish("category.mini-bag.label", "Minis")
    assert content.category_label("Mini Bag") == "Minis"
    # The canonical name still resolves the same URL/slug.
    from app.utils import slugify

    assert slugify("Mini Bag") == "mini-bag"


def test_category_helpers_tolerate_unknown_and_empty_categories(ctx) -> None:
    assert content.category_label("Categoría Inventada") == "Categoría Inventada"
    assert content.category_label("") == ""
    assert content.category_label(None) == ""  # type: ignore[arg-type]
    assert content.category_intro("Categoría Inventada") is None
    assert content.category_tagline("Categoría Inventada") == ""


# --- admin-facing state --------------------------------------------------------


def test_field_state_describes_default_published_and_draft(ctx) -> None:
    field = registry.FIELDS[KEY]

    state = content.field_state(KEY)
    assert state == {
        "field": field,
        "default": field.default,
        "published": None,
        "draft": None,
        "live": field.default,
        "value": field.default,
        "has_draft": False,
        "is_overridden": False,
        "returns_to_default": False,
        "previous": None,
        "has_previous": False,
    }

    _publish(KEY, "Publicado")
    _draft(KEY, "Borrador")
    with ctx.test_request_context("/"):
        state = content.field_state(KEY)
        assert state["published"] == "Publicado"
        assert state["draft"] == "Borrador"
        assert state["live"] == "Publicado"
        assert state["value"] == "Borrador"
        assert state["has_draft"] and state["is_overridden"]


def test_counters_only_count_real_pending_changes(ctx) -> None:
    group = registry.GROUPS_BY_KEY["home"]
    assert content.pending_draft_count(group) == 0
    assert content.override_count(group) == 0

    _draft(KEY, "Borrador")
    with ctx.test_request_context("/"):
        assert content.pending_draft_count(group) == 1
        assert content.pending_draft_count() == 1
        assert content.override_count(group) == 0


def test_the_documented_global_tokens_are_exactly_the_ones_that_resolve(ctx) -> None:
    """GLOBAL_TOKENS is what the editor hints promise; keep it honest."""
    from app.content.resolver import _global_tokens

    assert set(_global_tokens()) == set(registry.GLOBAL_TOKENS)


def test_two_requests_in_one_app_context_do_not_share_answers(app) -> None:
    """The per-request cache used to hang off `flask.g`, which belongs to the APP
    context — and an app context can span several requests (a CLI command, a test
    holding the app). The second request then inherited the first one's answers, so
    `?edit=1` with a valid session rendered as a plain page."""
    from app import auth
    from app.content import resolver

    with app.app_context():
        with app.test_request_context("/"):
            assert resolver.is_edit_mode() is False
        with app.test_request_context("/?edit=1"):
            auth.login()
            assert resolver.is_edit_mode() is True, "heredó la respuesta del request anterior"


def test_an_edit_made_between_two_requests_is_visible_to_the_second(app) -> None:
    with app.app_context():
        with app.test_request_context("/"):
            assert content.t(KEY) == registry.FIELDS[KEY].default
        with app.test_request_context("/"):
            _publish(KEY, "Nuevo")
        with app.test_request_context("/"):
            assert content.t(KEY) == "Nuevo"
