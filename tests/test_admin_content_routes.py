"""The /admin/content section: auth, the editor screens and the save/publish flow.

Everything is exercised through the HTTP surface (form posts, no JS), because the
editor is built as a plain form on purpose: if these pass, the section works with
JavaScript disabled too.
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from app.content import registry
from app.repositories import SiteTextRepository as Repo

KEY = "home.hero.subtitle"
RICH_KEY = "page.about.body"
URL_KEY = "global.instagram_url"
LINE_KEY = "nav.cta"

ADMIN_URLS = [
    "/admin/content/",       # the visual editor
    "/admin/content/list",   # the same copy as a list of forms
    "/admin/content/home",
    "/admin/content/home/preview",
]


def _state(app, key: str) -> dict:
    from app import content

    with app.test_request_context("/"):
        return content.field_state(key)


def _row(app, key: str):
    with app.test_request_context("/"):
        return Repo.get(key)


# --- auth ----------------------------------------------------------------------


@pytest.mark.parametrize("url", ADMIN_URLS)
def test_every_page_needs_a_session(client: FlaskClient, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "url", ["/admin/content/home", "/admin/content/publish", "/admin/content/discard"]
)
def test_every_mutation_needs_a_session(client: FlaskClient, url: str) -> None:
    response = client.post(url, data={"action": "save"})
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]


def test_an_anonymous_post_changes_nothing(app, client: FlaskClient) -> None:
    client.post("/admin/content/home", data={KEY: "hackeado", "action": "publish"})
    assert _row(app, KEY) is None


# --- screens -------------------------------------------------------------------


def test_the_index_lists_every_group(auth_client: FlaskClient) -> None:
    html = auth_client.get("/admin/content/list").get_data(as_text=True)
    # `class="…"` and not the bare name: the stylesheet is inlined into the page.
    assert html.count('class="ct-card-link"') == len(registry.GROUPS)
    for group in registry.GROUPS:
        assert group.title in html


def test_the_editor_renders_every_field_of_its_group(auth_client: FlaskClient) -> None:
    group = registry.GROUPS_BY_KEY["home"]
    html = auth_client.get("/admin/content/home").get_data(as_text=True)
    for field in group.fields:
        assert f'name="{field.key}"' in html, field.key
        assert field.label in html, field.key


def test_the_editor_shows_the_current_values(auth_client: FlaskClient) -> None:
    auth_client.post("/admin/content/home", data={KEY: "Un texto propio", "action": "publish"})
    html = auth_client.get("/admin/content/home").get_data(as_text=True)
    assert "Un texto propio" in html


def test_an_unknown_group_is_a_404(auth_client: FlaskClient) -> None:
    assert auth_client.get("/admin/content/inventado").status_code == 404
    assert auth_client.post("/admin/content/inventado", data={}).status_code == 404
    assert auth_client.get("/admin/content/inventado/preview").status_code == 404


def test_the_admin_section_is_never_indexable(auth_client: FlaskClient) -> None:
    for url in ADMIN_URLS:
        html = auth_client.get(url).get_data(as_text=True)
        assert 'content="noindex, nofollow"' in html, url


def test_both_admin_sections_are_reachable_from_the_header(auth_client: FlaskClient) -> None:
    html = auth_client.get("/admin/content/list").get_data(as_text=True)
    assert 'href="/admin/"' in html and 'href="/admin/content/"' in html


# --- saving --------------------------------------------------------------------


def test_saving_writes_a_draft_and_leaves_the_live_site_alone(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    response = auth_client.post(
        "/admin/content/home", data={KEY: "Bajada nueva", "action": "save"}, follow_redirects=True
    )
    assert response.status_code == 200
    state = _state(app, KEY)
    assert state["draft"] == "Bajada nueva"
    assert state["published"] is None
    assert "Bajada nueva" not in client.get("/").get_data(as_text=True)


def test_publishing_makes_it_live(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    auth_client.post(
        "/admin/content/home", data={KEY: "Bajada publicada", "action": "publish"},
        follow_redirects=True,
    )
    assert _state(app, KEY)["published"] == "Bajada publicada"
    assert "Bajada publicada" in client.get("/").get_data(as_text=True)


def test_saving_a_value_identical_to_the_live_one_stores_no_draft(
    app, auth_client: FlaskClient
) -> None:
    """Otherwise the 'sin publicar' counter would fill up with no-ops."""
    default = registry.FIELDS[KEY].default
    auth_client.post("/admin/content/home", data={KEY: default, "action": "save"})
    assert _row(app, KEY) is None


def test_crlf_from_the_textarea_does_not_count_as_a_change(app, auth_client: FlaskClient) -> None:
    field = registry.FIELDS["home.marquee.phrases"]
    auth_client.post(
        "/admin/content/home",
        data={"home.marquee.phrases": field.default.replace("\n", "\r\n"), "action": "save"},
    )
    assert _row(app, "home.marquee.phrases") is None


def test_fields_of_other_groups_are_untouched(app, auth_client: FlaskClient) -> None:
    auth_client.post(
        "/admin/content/home", data={KEY: "x", LINE_KEY: "No debería guardarse", "action": "save"}
    )
    assert _row(app, LINE_KEY) is None


def test_publishing_from_the_index_publishes_every_group(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    auth_client.post("/admin/content/home", data={KEY: "Uno", "action": "save"})
    auth_client.post("/admin/content/global", data={LINE_KEY: "Comprá", "action": "save"})
    auth_client.post("/admin/content/publish", follow_redirects=True)
    html = client.get("/").get_data(as_text=True)
    assert "Uno" in html and "Comprá" in html


def test_discarding_from_the_index_drops_every_pending_draft(
    app, auth_client: FlaskClient
) -> None:
    auth_client.post("/admin/content/home", data={KEY: "Uno", "action": "save"})
    auth_client.post("/admin/content/discard", follow_redirects=True)
    assert _row(app, KEY) is None


def test_discarding_one_group_keeps_the_others(app, auth_client: FlaskClient) -> None:
    auth_client.post("/admin/content/home", data={KEY: "Uno", "action": "save"})
    auth_client.post("/admin/content/global", data={LINE_KEY: "Dos", "action": "save"})
    auth_client.post("/admin/content/home", data={"action": "discard"}, follow_redirects=True)
    assert _row(app, KEY) is None
    assert _row(app, LINE_KEY).draft_value == "Dos"


# --- restore -------------------------------------------------------------------


def test_restore_drafts_the_original_without_losing_other_edits(
    app, auth_client: FlaskClient
) -> None:
    """The restore button is a submit: it posts the whole form, so whatever else the
    editor had typed must survive the round trip."""
    auth_client.post("/admin/content/home", data={KEY: "Editado", "action": "publish"})
    auth_client.post(
        "/admin/content/home",
        data={KEY: "Editado", "home.hero.eyebrow": "Otro cambio", "restore": KEY},
        follow_redirects=True,
    )
    assert _state(app, KEY)["draft"] == registry.FIELDS[KEY].default
    assert _state(app, "home.hero.eyebrow")["draft"] == "Otro cambio"


def test_restoring_and_publishing_puts_the_original_back(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    auth_client.post("/admin/content/home", data={KEY: "Editado", "action": "publish"})
    auth_client.post("/admin/content/home", data={KEY: "Editado", "restore": KEY})
    auth_client.post("/admin/content/home", data={"action": "publish"}, follow_redirects=True)

    row = _row(app, KEY)
    assert row.published_value is None  # back to the registry default
    assert row.draft_value is None
    # The row survives on purpose: it remembers the wording that was live, so
    # "Volver al texto anterior" still has somewhere to go.
    assert row.previous_value == "Editado"
    assert registry.FIELDS[KEY].default in client.get("/").get_data(as_text=True)


# --- validation ----------------------------------------------------------------


def test_a_value_over_the_limit_is_rejected(app, auth_client: FlaskClient) -> None:
    field = registry.FIELDS[KEY]
    response = auth_client.post(
        "/admin/content/home", data={KEY: "x" * (field.max_length + 1), "action": "save"}
    )
    assert response.status_code == 400
    assert "máximo" in response.get_data(as_text=True)
    assert _row(app, KEY) is None


def test_a_blank_single_line_field_is_rejected(app, auth_client: FlaskClient) -> None:
    response = auth_client.post("/admin/content/global", data={LINE_KEY: "   ", "action": "save"})
    assert response.status_code == 400
    assert _row(app, LINE_KEY) is None


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "no-es-una-url", "ftp://x.test"])
def test_a_bad_link_is_rejected(app, auth_client: FlaskClient, bad: str) -> None:
    response = auth_client.post("/admin/content/global", data={URL_KEY: bad, "action": "save"})
    assert response.status_code == 400
    assert _row(app, URL_KEY) is None


def test_a_rejected_submission_saves_nothing_at_all(app, auth_client: FlaskClient) -> None:
    """All-or-nothing: a half-saved screen is worse than a rejected one."""
    response = auth_client.post(
        "/admin/content/home",
        data={
            "home.hero.eyebrow": "Este está bien",
            KEY: "x" * 5000,  # this one is not
            "action": "save",
        },
    )
    assert response.status_code == 400
    assert _row(app, "home.hero.eyebrow") is None
    assert _row(app, KEY) is None


def test_a_rejected_submission_keeps_what_was_typed(auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/admin/content/home",
        data={"home.hero.eyebrow": "Lo que escribí", KEY: "x" * 5000, "action": "save"},
    )
    assert "Lo que escribí" in response.get_data(as_text=True)


def test_a_long_value_is_accepted_right_up_to_the_limit(app, auth_client: FlaskClient) -> None:
    field = registry.FIELDS[KEY]
    auth_client.post("/admin/content/home", data={KEY: "x" * field.max_length, "action": "save"})
    assert _row(app, KEY).draft_value == "x" * field.max_length


# --- preview screen ------------------------------------------------------------


def test_the_preview_screen_points_at_the_real_page(auth_client: FlaskClient) -> None:
    html = auth_client.get("/admin/content/home/preview").get_data(as_text=True)
    assert 'src="/?preview=1"' in html


def test_the_preview_offers_device_and_share_formats(auth_client: FlaskClient) -> None:
    from app.admin_content import PREVIEW_CARDS, PREVIEW_DEVICES

    html = auth_client.get("/admin/content/home/preview").get_data(as_text=True)
    for device in PREVIEW_DEVICES:
        assert f'data-width="{device["width"]}"' in html, device["key"]
        assert device["label"] in html
    for card in PREVIEW_CARDS:
        assert f'data-card="{card["key"]}"' in html, card["key"]


def test_the_product_preview_resolves_a_real_product(auth_client: FlaskClient) -> None:
    """The product group has no fixed URL: with no catalogue it must still render."""
    html = auth_client.get("/admin/content/product/preview").get_data(as_text=True)
    assert 'data-ct-iframe' in html
    assert "?preview=1" in html


def test_the_category_preview_falls_back_to_a_curated_slug(auth_client: FlaskClient) -> None:
    html = auth_client.get("/admin/content/category/preview").get_data(as_text=True)
    assert f'src="/categoria/{registry.CURATED_CATEGORY_SLUGS[0]}?preview=1"' in html


def test_publishing_from_the_preview_screen_works(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    auth_client.post("/admin/content/home", data={KEY: "Desde el preview", "action": "save"})
    auth_client.post("/admin/content/home", data={"action": "publish"}, follow_redirects=True)
    assert "Desde el preview" in client.get("/").get_data(as_text=True)
