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


# --- text sizes (flask-sitecopy 0.5.0) ---------------------------------------


def test_text_size_override_renders_a_size_wrapper(app: "Flask", client: "FlaskClient") -> None:
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        current_store().set_published("size:home.hero.title", "lg")
        save()
    html = client.get("/").get_data(as_text=True)
    assert "sc-s-lg" in html  # the sized value is wrapped and its rule is injected


# --- editable images: override-aware, no perf regression by default ----------


def test_hero_keeps_responsive_picture_by_default(client: "FlaskClient") -> None:
    html = client.get("/").get_data(as_text=True)
    # Default (no override): the optimized <picture> with AVIF/responsive srcset.
    assert "hero-tote-cognac-playa.avif" in html
    assert 'class="hero-img hero-media"' not in html


def test_hero_switches_to_editable_image_when_overridden(app: "Flask", client: "FlaskClient") -> None:
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        current_store().set_published("home.hero.image", "/media/sitecopy-uploads/custom.jpg")
        save()
    html = client.get("/").get_data(as_text=True)
    assert "/media/sitecopy-uploads/custom.jpg" in html


def test_is_overridden_helper(app: "Flask") -> None:
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        assert content.is_overridden("home.hero.image") is False
        current_store().set_published("home.hero.image", "/x.jpg")
        save()
        assert content.is_overridden("home.hero.image") is True


# --- uploads (flask-sitecopy 0.4.0) ------------------------------------------


def test_category_intro_and_tagline_helpers(app: "Flask") -> None:
    with app.test_request_context("/"):
        # Curated slug ("tote") has label/tagline/intro fields; unknown falls back.
        assert isinstance(content.category_tagline("Tote"), str)
        assert content.category_tagline("Inventada") == ""
        assert content.category_intro("Inventada") is None
        # Editable (marker-emitting) variants resolve without raising.
        assert content.category_tagline_editable("Inventada") == ""
        assert content.category_label_editable("Inventada") == "Inventada"
        assert content.category_intro_editable("Inventada") is None
        assert str(content.category_label_editable("Tote"))  # non-empty


def test_editor_pages_lists_home_statics_and_a_product(app: "Flask") -> None:
    with app.app_context():
        ProductRepository.create(title="Tote", price=45000, category="Tote")
        pages = content._editor_pages()
    paths = [p["path"] for p in pages]
    assert "/" in paths
    assert "/nosotras" in paths
    assert any(p.startswith("/producto/") for p in paths)


def test_registry_lookup_helpers() -> None:
    from app.content import registry

    assert registry.field_for("global.brand") is not None
    assert registry.field_for("no.existe") is None
    assert registry.group_for("home") is not None
    assert registry.group_for("no.existe") is None
    # allowed_tokens merges per-field tokens with the global ones.
    toks = registry.allowed_tokens("product.meta.title")
    assert "title" in toks and "brand" in toks
    # is_multiline distinguishes block fields from single-line ones.
    assert registry.FIELDS["seo.home.description"].is_multiline is True
    assert registry.FIELDS["global.brand"].is_multiline is False
    buckets = registry.groups_by_category()
    assert buckets and all(isinstance(v, list) for v in buckets.values())


def test_image_upload_endpoint_stores_and_returns_a_media_url(app: "Flask", auth_client: "FlaskClient") -> None:
    import io
    import re
    import struct
    import zlib

    def _png() -> bytes:
        sig = b"\x89PNG\r\n\x1a\n"

        def chunk(tag: bytes, data: bytes) -> bytes:
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

        return (
            sig
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
            + chunk(b"IEND", b"")
        )

    token = re.search(
        r'name="_sitecopy_csrf" value="([^"]+)"',
        auth_client.get("/admin/content/home").get_data(as_text=True),
    ).group(1)
    res = auth_client.post(
        "/admin/content/upload",
        data={
            "key": "home.hero.image",
            "_sitecopy_csrf": token,
            "file": (io.BytesIO(_png()), "hero.png", "image/png"),
        },
        headers={"X-Sitecopy-CSRF": token},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["url"].startswith("/media/sitecopy-uploads/")
