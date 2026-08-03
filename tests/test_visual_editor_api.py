"""The JSON endpoint the visual editor saves through.

Same rules as the form editor (validation, sanitizing, all-or-nothing, drafts before
publishing) — asserted separately because this is a different entry point, and the
one an attacker would reach for first.
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from app.content import registry
from app.repositories import SiteTextRepository as Repo

SAVE = "/admin/content/save"
KEY = "home.hero.subtitle"
RICH_KEY = "page.about.body"
URL_KEY = "global.instagram_url"


def _row(app, key: str):
    with app.test_request_context("/"):
        return Repo.get(key)


def _save(client: FlaskClient, changes: dict, action: str = "save"):
    return client.post(SAVE, json={"changes": changes, "action": action})


# --- happy path ----------------------------------------------------------------


def test_saving_stages_a_draft(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    response = _save(auth_client, {KEY: "Desde el editor visual"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True and body["saved"] == 1 and body["pending"] == 1

    row = _row(app, KEY)
    assert row.draft_value == "Desde el editor visual" and row.published_value is None
    assert "Desde el editor visual" not in client.get("/").get_data(as_text=True)


def test_publishing_makes_it_live(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    response = _save(auth_client, {KEY: "Publicado en vivo"}, action="publish")
    body = response.get_json()
    assert body["ok"] is True and body["published"] == 1 and body["pending"] == 0
    assert "Publicado en vivo" in client.get("/").get_data(as_text=True)


def test_several_fields_in_one_request(app, auth_client: FlaskClient) -> None:
    _save(auth_client, {KEY: "Uno", "nav.cta": "Dos", "home.hero.eyebrow": "Tres"})
    assert _row(app, KEY).draft_value == "Uno"
    assert _row(app, "nav.cta").draft_value == "Dos"
    assert _row(app, "home.hero.eyebrow").draft_value == "Tres"


def test_publishing_also_publishes_drafts_from_other_screens(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    """The button says "Publicar <n>" for everything pending, so it must publish it."""
    auth_client.post("/admin/content/global", data={"nav.cta": "Desde el form", "action": "save"})
    _save(auth_client, {KEY: "Desde el editor"}, action="publish")
    html = client.get("/").get_data(as_text=True)
    assert "Desde el form" in html and "Desde el editor" in html


def test_a_value_back_to_the_original_clears_the_draft(app, auth_client: FlaskClient) -> None:
    _save(auth_client, {KEY: "Cambiado"})
    assert _row(app, KEY) is not None
    _save(auth_client, {KEY: registry.FIELDS[KEY].default})
    assert _row(app, KEY) is None


def test_editing_one_line_of_a_list(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    """The editor splices the edited item back into the whole value client-side; the
    server just stores it."""
    _save(auth_client, {"home.feature.specs": "Uno\nDos\nTres"}, action="publish")
    html = client.get("/").get_data(as_text=True)
    assert "<li>Uno</li>" in html and "<li>Tres</li>" in html


# --- validation ----------------------------------------------------------------


def test_an_over_long_value_is_rejected(app, auth_client: FlaskClient) -> None:
    response = _save(auth_client, {KEY: "x" * 5000})
    assert response.status_code == 400
    assert response.get_json()["ok"] is False
    assert "máximo" in " ".join(response.get_json()["errors"])
    assert _row(app, KEY) is None


def test_a_blank_single_line_value_is_rejected(app, auth_client: FlaskClient) -> None:
    assert _save(auth_client, {"nav.cta": "  "}).status_code == 400
    assert _row(app, "nav.cta") is None


def test_a_bad_link_is_rejected(app, auth_client: FlaskClient) -> None:
    assert _save(auth_client, {URL_KEY: "javascript:alert(1)"}).status_code == 400
    assert _row(app, URL_KEY) is None


def test_one_bad_field_rejects_the_whole_request(app, auth_client: FlaskClient) -> None:
    response = _save(auth_client, {"home.hero.eyebrow": "Está bien", KEY: "x" * 5000})
    assert response.status_code == 400
    assert _row(app, "home.hero.eyebrow") is None
    assert _row(app, KEY) is None


def test_an_unknown_key_is_reported_not_stored(app, auth_client: FlaskClient) -> None:
    response = _save(auth_client, {"no.such.key": "x"})
    assert response.status_code == 400
    assert "no.such.key" in " ".join(response.get_json()["errors"])


@pytest.mark.parametrize("payload", [None, [], {"changes": "no soy un dict"}, {}])
def test_a_malformed_body_is_rejected(auth_client: FlaskClient, payload) -> None:
    response = auth_client.post(SAVE, json=payload)
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_an_empty_change_set_is_accepted_as_a_no_op(auth_client: FlaskClient) -> None:
    """Publishing with nothing edited locally still publishes what is pending."""
    response = _save(auth_client, {})
    assert response.status_code == 200 and response.get_json()["saved"] == 0


# --- security ------------------------------------------------------------------


def test_rich_html_is_sanitized_before_it_is_stored(app, auth_client: FlaskClient) -> None:
    _save(
        auth_client,
        {RICH_KEY: '<p>ok</p><script>alert(1)</script><img src=x onerror="alert(2)">'},
    )
    stored = _row(app, RICH_KEY).draft_value
    assert "<p>ok</p>" in stored
    assert "script" not in stored and "onerror" not in stored


def test_the_toolbar_formatting_survives_sanitizing(app, auth_client: FlaskClient) -> None:
    """contenteditable's execCommand output must not be stripped as unknown markup."""
    _save(auth_client, {RICH_KEY: "<p><b>Negrita</b> e <i>itálica</i></p><h2>Título</h2>"})
    stored = _row(app, RICH_KEY).draft_value
    assert "<b>Negrita</b>" in stored and "<i>itálica</i>" in stored and "<h2>Título</h2>" in stored


def test_saving_requires_a_session(app, client: FlaskClient) -> None:
    """A fetch() gets JSON back, not a redirect to the login PAGE — following that
    redirect is what made an expired session surface as `Unexpected token '<'`."""
    response = client.post(SAVE, json={"changes": {KEY: "hackeado"}, "action": "publish"})
    assert response.status_code == 401
    body = response.get_json()
    assert body["ok"] is False and body["reason"] == "auth"
    assert "sesión" in " ".join(body["errors"])
    assert _row(app, KEY) is None


def test_a_browser_navigation_still_gets_the_login_page(client: FlaskClient) -> None:
    """Only XHR gets JSON; a person typing the URL still lands on the login form."""
    response = client.get("/admin/content/")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]


def test_the_endpoint_is_post_only(auth_client: FlaskClient) -> None:
    assert auth_client.get(SAVE).status_code in (404, 405)


# --- the editor screen ---------------------------------------------------------


def test_the_editor_screen_frames_the_live_site(auth_client: FlaskClient) -> None:
    html = auth_client.get("/admin/content/").get_data(as_text=True)
    assert 'src="/?edit=1"' in html
    assert 'data-save-url="/admin/content/save"' in html


def test_the_editor_can_start_on_any_page(auth_client: FlaskClient) -> None:
    html = auth_client.get("/admin/content/?path=/nosotras").get_data(as_text=True)
    assert 'src="/nosotras?edit=1"' in html


def test_the_editor_shows_what_is_pending(auth_client: FlaskClient) -> None:
    _save(auth_client, {KEY: "Pendiente"})
    html = auth_client.get("/admin/content/").get_data(as_text=True)
    assert 'data-pending="1"' in html


def test_the_editor_degrades_to_the_list_without_javascript(auth_client: FlaskClient) -> None:
    html = auth_client.get("/admin/content/").get_data(as_text=True)
    assert "<noscript>" in html
    assert 'href="/admin/content/list"' in html


# --- regressions found by using the editor for real ---------------------------


def test_a_rejected_save_reports_which_fields_failed(auth_client: FlaskClient) -> None:
    """The editor highlights the offending text and scrolls the panel to it, so the
    message alone is not enough — it needs the keys."""
    response = _save(auth_client, {KEY: "x" * 5000, "nav.cta": "  ", "home.hero.eyebrow": "ok"})
    assert response.status_code == 400
    body = response.get_json()
    assert set(body["errorKeys"]) == {KEY, "nav.cta"}
    assert len(body["errors"]) == 2


def test_an_unknown_key_is_reported_with_its_key(auth_client: FlaskClient) -> None:
    body = _save(auth_client, {"no.such.key": "x"}).get_json()
    assert body["errorKeys"] == ["no.such.key"]


def test_the_editor_can_save_a_category_label(auth_client: FlaskClient, client: FlaskClient) -> None:
    _save(auth_client, {"category.tote.label": "Totes grandes"}, action="publish")
    assert '<h3 class="cat-name">Totes grandes</h3>' in client.get("/").get_data(as_text=True)
    # …and the category still lives at the same URL, with the new label as its H1.
    page = client.get("/categoria/tote")
    assert page.status_code == 200
    assert '<h1 class="section-title">Totes grandes</h1>' in page.get_data(as_text=True)


# --- publishing is scoped and reversible --------------------------------------


def test_publishing_only_touches_the_keys_the_editor_holds(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    """It used to publish every draft in the database, so a colleague's half-finished
    text went live under a confirm that never named it."""
    auth_client.post(
        "/admin/content/global", data={"footer.cta_eyebrow": "A MEDIO HACER", "action": "save"}
    )
    auth_client.post(
        SAVE, json={"changes": {KEY: "Lo mío"}, "action": "publish", "keys": [KEY]}
    )
    html = client.get("/").get_data(as_text=True)
    assert "Lo mío" in html
    assert "A MEDIO HACER" not in html
    assert _row(app, "footer.cta_eyebrow").draft_value == "A MEDIO HACER"


def test_the_response_lists_what_is_still_pending(auth_client: FlaskClient) -> None:
    """The panel renders pending keys from other pages out of this."""
    auth_client.post("/admin/content/global", data={"nav.cta": "Desde el form", "action": "save"})
    body = _save(auth_client, {KEY: "Desde el editor"}).get_json()
    assert set(body["pendingKeys"]) == {KEY, "nav.cta"}
    entry = body["pendingFields"]["nav.cta"]
    assert entry["label"] == registry.FIELDS["nav.cta"].label
    assert entry["raw"] == "Desde el form"
    assert entry["groupTitle"] and entry["type"] == "line"


def test_publishing_remembers_the_wording_it_replaced(app, auth_client: FlaskClient) -> None:
    _save(auth_client, {KEY: "Primera versión"}, action="publish")
    _save(auth_client, {KEY: "Segunda versión"}, action="publish")
    assert _row(app, KEY).previous_value == "Primera versión"


def test_reverting_puts_the_previous_wording_back(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    """"Restaurar original" goes to the factory text; this goes back one step, which
    is what someone who just published a typo actually wants."""
    _save(auth_client, {KEY: "La que me gustaba"}, action="publish")
    _save(auth_client, {KEY: "El error"}, action="publish")
    assert "El error" in client.get("/").get_data(as_text=True)

    response = auth_client.post("/admin/content/revert", json={"key": KEY})
    assert response.status_code == 200 and response.get_json()["ok"] is True
    assert "La que me gustaba" in client.get("/").get_data(as_text=True)


def test_reverting_needs_something_to_revert_to(auth_client: FlaskClient) -> None:
    response = auth_client.post("/admin/content/revert", json={"key": KEY})
    assert response.status_code == 400
    assert "anterior" in " ".join(response.get_json()["errors"])


def test_reverting_an_unknown_key_is_rejected(auth_client: FlaskClient) -> None:
    assert auth_client.post("/admin/content/revert", json={"key": "no.existe"}).status_code == 400


# --- a page body can no longer be emptied by accident -------------------------


def test_a_rich_body_cannot_be_left_blank(app, auth_client: FlaskClient) -> None:
    """Select-all + type used to store `<p></p>` and publish a blank page, cheerfully."""
    for blank in ("", "   ", "<p></p>", "<p><br></p>", "<h2></h2>"):
        response = _save(auth_client, {RICH_KEY: blank})
        assert response.status_code == 400, blank
        assert RICH_KEY in response.get_json()["errorKeys"], blank
    assert _row(app, RICH_KEY) is None
