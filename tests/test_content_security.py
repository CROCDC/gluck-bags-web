"""Security properties of editable copy.

Letting an admin write strings that land in every public page creates three risks:
stored XSS, unpublished copy leaking, and a forged request editing the site. Each
one is pinned here — including the case where the value did NOT come through the
admin form (a restored backup, a manual UPDATE), which is why rich text is
sanitized on render and not only on save.
"""

from __future__ import annotations

import json
import re

import pytest
from flask.testing import FlaskClient

from app.models import SiteText
from app.repositories import SiteTextRepository as Repo

XSS = '<img src=x onerror="alert(1)"><script>alert(2)</script>'


def _write_raw(app, key: str, value: str) -> None:
    """Write straight to the table, bypassing the admin form and its sanitizer."""
    from app.factory import db

    with app.app_context():
        row = SiteText(key=key, published_value=value)
        db.session.merge(row)
        db.session.commit()


# --- stored XSS ----------------------------------------------------------------


def test_rich_copy_from_the_form_is_sanitized_on_save(app, auth_client: FlaskClient) -> None:
    auth_client.post(
        "/admin/content/page-about",
        data={"page.about.body": f"<p>hola</p>{XSS}", "action": "publish"},
    )
    with app.test_request_context("/"):
        stored = Repo.get("page.about.body").published_value
    assert "script" not in stored and "onerror" not in stored
    assert "<p>hola</p>" in stored


def test_rich_copy_written_around_the_form_is_still_neutralized_on_render(
    app, client: FlaskClient
) -> None:
    """Defense in depth: the value never came through the admin, and still can't run."""
    _write_raw(app, "page.about.body", f"<p>ok</p>{XSS}")
    html = client.get("/nosotras").get_data(as_text=True)
    assert "<p>ok</p>" in html
    assert "onerror" not in html
    assert "<script>alert(2)</script>" not in html


def test_plain_copy_is_escaped_not_rendered_as_markup(app, client: FlaskClient) -> None:
    _write_raw(app, "home.hero.subtitle", XSS)
    html = client.get("/").get_data(as_text=True)
    assert XSS not in html
    assert "&lt;img" in html or "&lt;script" in html


def test_copy_cannot_break_out_of_an_html_attribute(app, client: FlaskClient) -> None:
    _write_raw(app, "home.hero.image_alt", '" onload="alert(1)')
    html = client.get("/").get_data(as_text=True)
    assert 'onload="alert(1)' not in html
    assert "&#34;" in html or "&quot;" in html


def test_copy_cannot_break_out_of_the_json_ld_block(app, client: FlaskClient) -> None:
    _write_raw(app, "global.brand", '</script><script>alert(1)</script>')
    html = client.get("/").get_data(as_text=True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert blocks, "the home should still emit its JSON-LD"
    for block in blocks:
        assert "<script" not in block
        json.loads(block)  # still valid JSON


def test_copy_cannot_break_out_of_the_cart_strings_json(app, client: FlaskClient) -> None:
    _write_raw(app, "cart.page.empty", '</script><script>alert(1)</script>')
    html = client.get("/").get_data(as_text=True)
    payload = re.search(r'id="cartStrings">(.*?)</script>', html, re.S)
    assert payload is not None
    assert "<script" not in payload.group(1)
    json.loads(payload.group(1))


def test_a_link_field_cannot_become_a_javascript_url(app, client: FlaskClient) -> None:
    _write_raw(app, "global.instagram_url", "javascript:alert(1)")
    html = client.get("/").get_data(as_text=True)
    assert 'href="javascript:alert(1)"' not in html


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,<script>x</script>"])
def test_the_form_refuses_a_dangerous_link(app, auth_client: FlaskClient, bad: str) -> None:
    response = auth_client.post(
        "/admin/content/global", data={"global.instagram_url": bad, "action": "publish"}
    )
    assert response.status_code == 400
    with app.test_request_context("/"):
        assert Repo.get("global.instagram_url") is None


# --- unpublished copy stays private --------------------------------------------


def test_preview_mode_requires_an_admin_session(
    auth_client: FlaskClient, client: FlaskClient
) -> None:
    auth_client.post(
        "/admin/content/home", data={"home.hero.subtitle": "SECRETO", "action": "save"}
    )
    assert "SECRETO" not in client.get("/?preview=1").get_data(as_text=True)
    assert "SECRETO" in auth_client.get("/?preview=1").get_data(as_text=True)


def test_a_shared_preview_link_leaks_nothing_after_logout(auth_client: FlaskClient) -> None:
    auth_client.post(
        "/admin/content/home", data={"home.hero.subtitle": "SECRETO", "action": "save"}
    )
    auth_client.post("/admin/logout")
    assert "SECRETO" not in auth_client.get("/?preview=1").get_data(as_text=True)


def test_a_preview_page_is_marked_noindex_for_crawlers(auth_client: FlaskClient) -> None:
    response = auth_client.get("/?preview=1")
    assert response.headers.get("X-Robots-Tag") == "noindex, nofollow"


# --- request forgery -----------------------------------------------------------


def test_the_session_cookie_blocks_cross_site_posts(app) -> None:
    """SameSite=Lax is the CSRF defense for the whole admin: the cookie is not sent
    on a cross-site POST, so a forged form cannot act as the logged-in admin."""
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_every_content_mutation_is_a_post(app) -> None:
    """State changes must never be reachable with a GET — that is what makes the
    SameSite cookie above an actual defense (and keeps a crawler from publishing)."""
    writers = {"group_save", "publish_all", "discard_all", "save_changes", "revert"}
    seen = set()
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/admin/content"):
            continue
        name = rule.endpoint.split(".")[-1]
        seen.add(name)
        if name in writers:
            assert "POST" in rule.methods and "GET" not in rule.methods, rule.endpoint
        else:
            assert "POST" not in rule.methods, rule.endpoint
    assert writers <= seen


def test_a_get_on_a_write_url_publishes_nothing(app, auth_client: FlaskClient) -> None:
    """`/admin/content/publish` also matches the `<group_key>` GET rule, so it answers
    404 rather than 405 — what matters is that nothing is written either way."""
    auth_client.post(
        "/admin/content/home", data={"home.hero.subtitle": "SIN PUBLICAR", "action": "save"}
    )
    for url in ("/admin/content/publish", "/admin/content/discard", "/admin/content/save", "/admin/content/revert"):
        assert auth_client.get(url).status_code in (404, 405), url
    with app.test_request_context("/"):
        row = Repo.get("home.hero.subtitle")
    assert row is not None and row.draft_value == "SIN PUBLICAR" and row.published_value is None


# --- resource limits -----------------------------------------------------------


def test_a_giant_paste_is_rejected_rather_than_stored(app, auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/admin/content/page-about",
        data={"page.about.body": "<p>x</p>" * 20000, "action": "save"},
    )
    assert response.status_code == 400
    with app.test_request_context("/"):
        assert Repo.get("page.about.body") is None
