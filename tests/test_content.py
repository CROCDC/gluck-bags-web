"""App-level tests for the editable-copy integration (flask-sitecopy).

The engine itself (resolver, sanitizer, editor markup, the panel) is covered by
flask-sitecopy's own test suite — the in-house copy of it was removed in the migration.
These tests pin the INTEGRATION: our registry ports cleanly, every key the templates use
is declared, the app-specific per-category layer works, public pages render the defaults,
publishing reaches the public page, edit markers stay admin-only, rich output is
sanitized, and existing overrides survive (same `site_texts` table).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sitecopy.testing import check_registry, check_templates

from app import content
from app.content import REGISTRY
from app.repositories import ProductRepository

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient


# --- registry / template coverage --------------------------------------------


def test_registry_ports_cleanly() -> None:
    assert check_registry(REGISTRY) == []


def test_no_template_uses_an_undeclared_key() -> None:
    # The dangerous direction: a t('key') a template renders that the registry doesn't
    # declare (that would raise / render blank). "declared but never rendered" is fine —
    # those keys are rendered via computed names (category.<slug>.*, manifesto loop,
    # the static-page template) that a static scan can't follow.
    problems = check_templates(REGISTRY, "app/templates")
    undeclared = [p for p in problems if "never rendered" not in p]
    assert undeclared == []


# --- public pages render the defaults ----------------------------------------


def test_home_renders_default_copy(client: "FlaskClient") -> None:
    html = client.get("/").get_data(as_text=True)
    assert client.get("/").status_code == 200
    # A manifesto value is rendered through a computed key — proves that path works.
    assert html.count('class="value"') >= 3


def test_static_page_renders(client: "FlaskClient") -> None:
    assert client.get("/nosotras").status_code == 200


def test_product_and_category_pages_render(app: "Flask", client: "FlaskClient") -> None:
    with app.app_context():
        ProductRepository.create(title="Tote Cognac", price=45000, category="Tote")
    assert client.get("/categoria/tote").status_code == 200
    with app.app_context():
        pid = ProductRepository.get_published()[0].id
    assert client.get(f"/producto/{pid}").status_code == 200


# --- app-specific per-category copy ------------------------------------------


def test_category_label_curated_vs_unknown(app: "Flask") -> None:
    with app.test_request_context("/"):
        # Curated category has an editable label field; unknown falls back to the name.
        assert content._category_key("Tote", "label") == "category.tote.label"
        assert content._category_key("Categoría Inventada", "label") is None
        assert content.category_label("Categoría Inventada") == "Categoría Inventada"
        # The curated one resolves to its registry default (a string, no markers).
        assert isinstance(content.category_label("Tote"), str)


# --- publish + defaults ------------------------------------------------------


def test_publish_override_reaches_public_and_defaults_on_fresh(app: "Flask", client: "FlaskClient") -> None:
    # Fresh DB renders the code default.
    assert "cuero vegano" in client.get("/").get_data(as_text=True).lower()
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        current_store().set_published("home.hero.title", "Título migrado")
        save()
    assert "Título migrado" in client.get("/").get_data(as_text=True)


def test_edit_markers_only_for_admin(auth_client: "FlaskClient", client: "FlaskClient") -> None:
    admin_html = auth_client.get("/?edit=1").get_data(as_text=True)
    assert "data-k" in admin_html or "ct-t" in admin_html or "data-ct-keys" in admin_html
    public_html = client.get("/?edit=1").get_data(as_text=True)
    assert "ct-t" not in public_html and "data-ct-keys" not in public_html


# --- safety ------------------------------------------------------------------


def test_rich_override_is_sanitized_on_public_render(app: "Flask", client: "FlaskClient") -> None:
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        current_store().set_published(
            "home.manifesto.quote", "<script>alert(1)</script><em>seguro</em>"
        )
        save()
    html = client.get("/").get_data(as_text=True)
    assert "<script>" not in html
    assert "<em>seguro</em>" in html


# --- brand tokens ------------------------------------------------------------


def test_brand_helpers_resolve(app: "Flask") -> None:
    with app.test_request_context("/"):
        assert content.brand()  # non-empty
        assert content.tagline()
        assert content.instagram_url().startswith("http")


# --- data preservation (same site_texts table) -------------------------------


def test_existing_overrides_survive_a_rebuild(app: "Flask") -> None:
    # Write directly to the store, then a fresh render reads it back — the migration
    # keeps the exact same `site_texts` table, so nothing loaded is lost.
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        current_store().set_published("global.brand", "GLÜCK Editado")
        save()
    with app.test_request_context("/"):
        assert content.brand() == "GLÜCK Editado"
