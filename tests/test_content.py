"""Tests for the flask-sitecopy content layer (app/content_registry.py + wiring).

Covers the library's own three CI checks (registry is sound, every t() key is declared
and every declared key is rendered, and the response rewrite still sees the HTML) plus
the integration: the panel is behind the admin login, edit markers only reach an admin,
and a published override shows on the public page while the defaults render untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sitecopy.testing import (
    check_registry,
    check_response_pipeline,
    check_templates,
)

from app.content_registry import REGISTRY

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient


# --- the library's own CI checks ---------------------------------------------


def test_registry_is_sound() -> None:
    assert check_registry(REGISTRY) == []


def test_every_key_is_rendered_and_every_rendered_key_exists() -> None:
    # Registry keys ↔ t('…') calls in the templates must match exactly.
    assert check_templates(REGISTRY, "app/templates") == []


def test_response_rewrite_still_sees_the_html(app: "Flask") -> None:
    # Guards the size-render pipeline: nothing (e.g. Compress) must gzip the body
    # before sitecopy's rewrite runs.
    with app.app_context():
        assert check_response_pipeline(app, "/", key="home.hero.title") == []


# --- defaults render (a fresh DB shows exactly the registry) ------------------


def test_home_renders_registry_defaults(client: "FlaskClient") -> None:
    html = client.get("/").get_data(as_text=True)
    assert "Cuero vegano · Sin costuras" in html          # hero.eyebrow
    assert "Cuatro siluetas, infinitas combinaciones" in html  # categorias.title
    assert "Lo último de GLÜCK" in html                   # shop.title ({brand} token)
    assert "te sigue<br>a todas partes" in html           # rich renders as HTML
    assert "<em>GLÜCK</em>" in html                       # rich + token in manifesto


# --- auth gating --------------------------------------------------------------


def test_panel_requires_admin_login(client: "FlaskClient") -> None:
    # Anonymous is redirected to a login (reuses the site's admin session).
    assert client.get("/admin/content/").status_code == 302


def test_panel_opens_for_logged_in_admin(auth_client: "FlaskClient") -> None:
    assert auth_client.get("/admin/content/").status_code == 200
    assert auth_client.get("/admin/content/list").status_code == 200


# --- edit markers are admin-only ---------------------------------------------


def test_edit_markers_only_for_admin(app: "Flask", auth_client: "FlaskClient", client: "FlaskClient") -> None:
    admin_html = auth_client.get("/?edit=1").get_data(as_text=True)
    assert "data-k" in admin_html or "ct-t" in admin_html or "data-ct-keys" in admin_html
    # A public visitor never sees the editing machinery, even with ?edit=1.
    public_html = client.get("/?edit=1").get_data(as_text=True)
    assert "ct-t" not in public_html and "data-ct-keys" not in public_html


# --- publishing an override reaches the public page ---------------------------


def test_published_override_shows_on_public_page(app: "Flask", client: "FlaskClient") -> None:
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        current_store().set_published("home.hero.title", "Bolsos que duran toda la vida")
        save()
    html = client.get("/").get_data(as_text=True)
    assert "Bolsos que duran toda la vida" in html
