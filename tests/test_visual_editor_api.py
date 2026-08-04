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


def _save(client: FlaskClient, changes: dict, action: str = "save", keys=None):
    """Post the way the editor does: it names the keys it is publishing.

    Publishing with no `keys` deliberately publishes NOTHING now — the endpoint used
    to fall back to "every draft in the database", which is how a colleague's parked
    text went live under a confirm that never mentioned it.
    """
    payload = {"changes": changes, "action": action}
    payload["keys"] = list(changes) if keys is None else keys
    return client.post(SAVE, json=payload)


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


def test_publishing_without_naming_keys_publishes_nothing(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    """A missing or malformed `keys` must mean "nothing", not "everything".

    This test used to assert the opposite — that publishing swept up every draft in
    the database — while another test in this file asserted the fix. Two tests, one
    endpoint, contradictory docstrings.
    """
    auth_client.post("/admin/content/global", data={"nav.cta": "Desde el form", "action": "save"})
    response = auth_client.post(
        SAVE, json={"changes": {}, "action": "publish"}  # no `keys`
    )
    assert response.get_json()["published"] == 0
    assert "Desde el form" not in client.get("/").get_data(as_text=True)
    assert _row(app, "nav.cta").draft_value == "Desde el form"


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
    _save(auth_client, {KEY: "Lo mío"}, action="publish", keys=[KEY])
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


def test_reverting_only_drafts_the_previous_wording(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    """It used to publish on the spot, from a link that looked exactly like the one
    beside it. Nothing changes the public site except Publicar."""
    _save(auth_client, {KEY: "La que me gustaba"}, action="publish")
    _save(auth_client, {KEY: "El error"}, action="publish")
    assert "El error" in client.get("/").get_data(as_text=True)

    response = auth_client.post("/admin/content/revert", json={"key": KEY})
    assert response.status_code == 200 and response.get_json()["ok"] is True
    assert response.get_json()["values"] == {KEY: "La que me gustaba"}
    # Drafted, not live.
    assert "El error" in client.get("/").get_data(as_text=True)
    assert _row(app, KEY).draft_value == "La que me gustaba"
    assert KEY in response.get_json()["pendingKeys"]

    _save(auth_client, {}, action="publish", keys=[KEY])
    assert "La que me gustaba" in client.get("/").get_data(as_text=True)


def test_undoing_a_first_publish_goes_back_to_the_factory_text(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    """`previous_value` is NULL after a first publish. The step back existed — it was
    called "Volver al texto original" — but no single Deshacer could find it, so the
    endpoint spells the rule out: what an override replaced was the factory text."""
    _save(auth_client, {KEY: "Publicado por primera vez"}, action="publish")
    assert _row(app, KEY).previous_value is None

    response = auth_client.post("/admin/content/revert", json={"key": KEY})
    assert response.status_code == 200
    assert response.get_json()["values"] == {KEY: registry.DEFAULTS[KEY]}

    _save(auth_client, {}, action="publish", keys=[KEY])
    assert registry.DEFAULTS[KEY] in client.get("/").get_data(as_text=True)
    # And the mistake is still one step away, so it can be brought back.
    assert _row(app, KEY).previous_value == "Publicado por primera vez"


def test_a_whole_publish_can_be_undone_at_once(app, auth_client: FlaskClient) -> None:
    """The Deshacer in the toolbar takes back everything that Publicar put live."""
    _save(auth_client, {KEY: "Uno", "nav.cta": "Dos"}, action="publish")
    response = auth_client.post(
        "/admin/content/revert", json={"keys": [KEY, "nav.cta", "no.existe"]}
    )
    assert response.status_code == 200
    assert set(response.get_json()["values"]) == {KEY, "nav.cta"}
    assert _row(app, KEY).draft_value == registry.DEFAULTS[KEY]
    assert _row(app, "nav.cta").draft_value == registry.DEFAULTS["nav.cta"]


def test_undoing_twice_leaves_the_same_draft(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    """The old swap made a double click publish and unpublish the live site."""
    _save(auth_client, {KEY: "Uno"}, action="publish")
    _save(auth_client, {KEY: "Dos"}, action="publish")

    for _ in range(2):
        assert auth_client.post("/admin/content/revert", json={"key": KEY}).status_code == 200
        assert _row(app, KEY).draft_value == "Uno"
    assert "Dos" in client.get("/").get_data(as_text=True)


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


# --- regressions from the code review -----------------------------------------


def test_a_rich_value_is_never_stored_over_its_own_cap(app, auth_client: FlaskClient) -> None:
    """`sanitize()` re-escapes &, < and >, so it GROWS the value. Validating before it
    let a value be stored over the cap, after which the same screen refused to save
    what it was displaying."""
    field = registry.FIELDS[RICH_KEY]
    payload = "<p>" + ("&" * (field.max_length - 20)) + "</p>"
    assert len(payload) <= field.max_length

    response = _save(auth_client, {RICH_KEY: payload})
    if response.status_code == 200:
        stored = _row(app, RICH_KEY).draft_value
        assert len(stored) <= field.max_length, f"guardó {len(stored)} con tope {field.max_length}"
    else:
        assert RICH_KEY in response.get_json()["errorKeys"]


@pytest.mark.parametrize("value", [["uno", "dos"], {"a": 1}, 12345, True, [{"x": ["y"]}], None])
def test_a_non_string_value_is_rejected(app, auth_client: FlaskClient, value) -> None:
    """JSON hands us lists and dicts; `str()` stored their Python repr and published
    `['uno', 'dos']` as the site's <h1>."""
    response = _save(auth_client, {KEY: value})
    assert response.status_code == 400, value
    assert _row(app, KEY) is None


def test_a_huge_key_list_does_not_take_the_endpoint_down(auth_client: FlaskClient) -> None:
    """The list went straight into an SQL `IN (…)`, which 500s past ~32k entries."""
    response = auth_client.post(
        SAVE, json={"changes": {}, "action": "publish", "keys": ["nav.cta"] * 40000}
    )
    assert response.status_code == 200


def test_only_a_bounded_number_of_errors_comes_back(auth_client: FlaskClient) -> None:
    response = auth_client.post(
        SAVE, json={"changes": {f"no.existe.{n}": "x" for n in range(500)}, "action": "save"}
    )
    assert response.status_code == 400
    assert len(response.get_json()["errors"]) <= 20


def test_undoing_an_undo_still_works(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    """Going back is not a one-way door either: publishing the drafted previous wording
    records the one it replaced, so the trip can be made in both directions."""
    _save(auth_client, {KEY: "Uno"}, action="publish")
    _save(auth_client, {KEY: "Dos"}, action="publish")

    auth_client.post("/admin/content/revert", json={"key": KEY})
    _save(auth_client, {}, action="publish", keys=[KEY])
    assert "Uno" in client.get("/").get_data(as_text=True)
    assert _row(app, KEY).previous_value == "Dos"

    auth_client.post("/admin/content/revert", json={"key": KEY})
    _save(auth_client, {}, action="publish", keys=[KEY])
    assert "Dos" in client.get("/").get_data(as_text=True)


def test_reverting_replaces_a_pending_draft(app, auth_client: FlaskClient) -> None:
    """The undo IS the draft now, so whatever was parked there loses: leaving both
    would mean the next Publicar quietly ignores the undo."""
    _save(auth_client, {KEY: "Uno"}, action="publish")
    _save(auth_client, {KEY: "Dos"}, action="publish")
    _save(auth_client, {KEY: "Borrador pendiente"})

    body = auth_client.post("/admin/content/revert", json={"key": KEY}).get_json()
    assert body["values"] == {KEY: "Uno"}
    assert _row(app, KEY).draft_value == "Uno"


@pytest.mark.parametrize(
    "path", ["javascript:alert(1)//", "https://evil.example/phish", "//evil.example", "  ", "/no-existe-en-el-sitio"]
)
def test_the_canvas_can_only_be_pointed_at_this_site(auth_client: FlaskClient, path: str) -> None:
    """`?path` went straight into the iframe's src, so a crafted link ran script in
    the admin's own origin — or embedded a foreign origin inside the admin chrome."""
    html = auth_client.get(f"/admin/content/?path={path}").get_data(as_text=True)
    assert 'src="/?edit=1"' in html, path
    assert "evil.example" not in html and "javascript:" not in html


@pytest.mark.parametrize("field_key", ["home.hero.subtitle", "home.marquee.phrases", "seo.home.description"])
def test_no_field_can_be_published_blank(app, auth_client: FlaskClient, field_key: str) -> None:
    """An empty `text` shipped an empty <h1> and an empty meta description; an empty
    `lines` emptied the marquee. The guard only covered the two types that were tested."""
    assert _save(auth_client, {field_key: "   "}).status_code == 400
    assert _row(app, field_key) is None


def test_a_broken_tag_is_refused_instead_of_silently_eating_the_page(
    app, auth_client: FlaskClient
) -> None:
    """An unterminated <script>/<svg> takes the rest of the value with it, and the
    result is what gets stored — the page loses its content and the editor is told
    everything went fine."""
    response = _save(
        auth_client,
        {RICH_KEY: "<p>Un primer párrafo bastante largo para que cuente.</p><svg><p>y todo esto se perdía</p>"},
    )
    assert response.status_code == 400
    assert RICH_KEY in response.get_json()["errorKeys"]
    assert "sin cerrar" in " ".join(response.get_json()["errors"])
    assert _row(app, RICH_KEY) is None
