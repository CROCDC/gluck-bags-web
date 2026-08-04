"""`{tokens}` are machinery, and the editor teaches them without checking them.

The panel labels the brand field "Marca" and the in-place tip says "dejá los {textos
entre llaves} tal cual", so writing `{marca}` is the natural move — and it used to be
published verbatim into the <h1>. Nothing downstream can catch it: the resolver leaves
an unknown token alone on purpose, so a stray brace can never raise mid-render.

The risky half of the guard is the token MAP: a field whose call site passes `title` but
that is not declared here becomes unsaveable. The first two tests hold the map to the
real call sites, in both directions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from app.admin_content import _validate
from app.content import registry, resolver
from app.repositories import SiteTextRepository as Repo

APP_DIR = Path(__file__).resolve().parent.parent / "app"
KEY = "home.hero.subtitle"


def _row(app, key: str):
    with app.test_request_context("/"):
        return Repo.get(key)


def _call_sites() -> dict[str, set[str]]:
    """`key -> the keyword arguments its t()/t_plain() call sites pass`.

    Nested calls are blanked before the arguments are read: `t("a", x=t("b", y=…))`
    would otherwise hand `y` to `a`.
    """
    found: dict[str, set[str]] = {}
    pattern = re.compile(r"""t(?:_plain|_lines)?\(\s*["']([a-z0-9_.]+)["']""")
    for path in APP_DIR.rglob("*"):
        if path.suffix not in (".py", ".html", ".js") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            key, depth, chars = match.group(1), 1, []
            for char in text[match.end() :]:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        break
                chars.append(" " if depth > 1 else char)
            names = set(re.findall(r"[,(]\s*([a-z_][a-z0-9_]*)\s*=", "(" + "".join(chars)))
            found.setdefault(key, set()).update(names)
    return found


def test_every_declared_field_token_is_really_passed_by_a_call_site() -> None:
    """A token declared but never passed is copy that renders as a literal brace."""
    sites = _call_sites()
    stale = {
        key: sorted(set(names) - sites.get(key, set()))
        for key, names in registry.FIELD_TOKENS.items()
        if set(names) - sites.get(key, set())
    }
    assert not stale, f"declarados pero nadie los pasa: {stale}"


def test_every_token_a_call_site_passes_is_declared() -> None:
    """The dangerous direction: an undeclared token makes its field unsaveable."""
    missing = {
        key: sorted(names - set(registry.FIELD_TOKENS.get(key, ())))
        for key, names in _call_sites().items()
        if key in registry.FIELDS and names - set(registry.FIELD_TOKENS.get(key, ()))
    }
    assert not missing, f"un call site los pasa pero no están declarados: {missing}"


def test_a_field_token_never_shadows_a_global_one() -> None:
    """Both would resolve, but the message would promise the wrong one."""
    for key, names in registry.FIELD_TOKENS.items():
        assert not set(names) & set(registry.GLOBAL_TOKENS), key


@pytest.mark.parametrize("key", sorted(registry.FIELDS))
def test_every_registry_default_is_accepted(app, key: str) -> None:
    """The guard would be unshippable if it rejected the copy that ships in the repo."""
    with app.test_request_context("/"):
        assert _validate(registry.FIELDS[key], registry.FIELDS[key].default) is None


def test_unknown_tokens_are_listed_once_in_the_order_they_appear() -> None:
    assert resolver.unknown_tokens(KEY, "{marca} y {tienda} y {marca}") == ["marca", "tienda"]
    assert resolver.unknown_tokens(KEY, "{brand} y {tagline}") == []
    # Per-field: `{title}` is real on a product page and nowhere else.
    assert resolver.unknown_tokens("product.meta.title", "{title}") == []
    assert resolver.unknown_tokens("home.hero.title", "{title}") == ["title"]


@pytest.mark.parametrize("value", ["{tagline", "Hola }", "{}", "50% {descuento", "{MARCA}"])
def test_a_brace_that_is_not_a_token_is_caught(value: str) -> None:
    """`{tagline` never reaches a lookup at all, so only the leftover brace shows it."""
    assert resolver.has_stray_brace(value) is True


def test_a_well_formed_token_leaves_no_stray_brace() -> None:
    assert resolver.has_stray_brace("Bolsos de {brand}, desde {year}") is False


def test_an_invented_token_is_refused_and_nothing_is_stored(app, auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/admin/content/save",
        json={"changes": {KEY: "Bolsos de {marca}"}, "action": "publish", "keys": [KEY]},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert KEY in body["errorKeys"]
    message = " ".join(body["errors"])
    # It names what she wrote AND what she could have written.
    assert "{marca}" in message and "{brand}" in message and "{tagline}" in message
    assert _row(app, KEY) is None


def test_the_message_offers_the_tokens_of_that_field(auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/admin/content/save",
        json={"changes": {"product.meta.title": "{titulo}"}, "action": "save"},
    )
    message = " ".join(response.get_json()["errors"])
    assert "{title}" in message and "{category}" in message


def test_an_unclosed_brace_is_refused(app, auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/admin/content/save", json={"changes": {KEY: "Hecho por {tagline"}, "action": "save"}
    )
    assert response.status_code == 400
    assert _row(app, KEY) is None


def test_a_legitimate_token_still_saves(app, auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/admin/content/save",
        json={"changes": {KEY: "Bolsos de {brand}"}, "action": "save", "keys": [KEY]},
    )
    assert response.status_code == 200
    assert _row(app, KEY).draft_value == "Bolsos de {brand}"


def test_the_form_editor_refuses_it_too(app, auth_client: FlaskClient) -> None:
    """Two doors into the same table; the JSON one is not the only one."""
    response = auth_client.post(
        "/admin/content/home", data={KEY: "Bolsos de {marca}", "action": "save"}
    )
    assert response.status_code == 400
    assert "{marca}" in response.get_data(as_text=True)
    assert _row(app, KEY) is None


def test_publishing_re_checks_a_draft_this_request_never_wrote(
    app, auth_client: FlaskClient, client: FlaskClient
) -> None:
    """Publish promotes drafts without re-running the save-time rules, and it publishes
    drafts parked by somebody else — or written before a rule existed."""
    with app.test_request_context("/"):
        Repo.set_draft(KEY, "Lo último de {marca}")
        Repo.save()

    response = auth_client.post(
        "/admin/content/save", json={"changes": {}, "action": "publish", "keys": [KEY]}
    )
    assert response.status_code == 400
    assert "{marca}" in " ".join(response.get_json()["errors"])
    assert "{marca}" not in client.get("/").get_data(as_text=True)
    assert _row(app, KEY).published_value is None


def test_publishing_the_whole_site_re_checks_too(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    with app.test_request_context("/"):
        Repo.set_draft(KEY, "Lo último de {marca}")
        Repo.save()

    auth_client.post("/admin/content/publish", follow_redirects=True)
    assert _row(app, KEY).published_value is None
    assert "{marca}" not in client.get("/").get_data(as_text=True)
