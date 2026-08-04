"""Edit mode: the markers the resolver emits and the markup they become.

This is the machinery the visual editor stands on. Two properties matter above all
and are asserted from several angles:

1. **Nothing leaks.** A normal request — logged out, logged in, even in preview —
   must be byte-for-byte free of markers, wrappers and the manifest.
2. **Every string stays traceable.** Whatever the template did with a value (text,
   attribute, <title>), the editor must be able to map it back to its key.
"""

from __future__ import annotations

import json
import re

import pytest
from flask.testing import FlaskClient

from app.content import registry
from app.content.editor_markup import transform
from app.content.resolver import EDIT_END, EDIT_SEP, EDIT_START

MARKERS = (EDIT_START, EDIT_SEP, EDIT_END)


def mark(key: str, value: str) -> str:
    return f"{EDIT_START}{key}{EDIT_SEP}{value}{EDIT_END}"


def _manifest(html: str) -> dict:
    payload = re.search(r'id="ctManifest">(.*?)</script>', html, re.S)
    assert payload is not None, "the edit-mode page should carry its manifest"
    return json.loads(payload.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))


# --- the transform -------------------------------------------------------------


def test_a_marker_in_visible_text_becomes_an_editable_node() -> None:
    html, inline, hidden = transform(f"<p>{mark('nav.cta', 'Comprar')}</p>")
    assert html == '<p><ct-t data-k="nav.cta" data-t="line">Comprar</ct-t></p>'
    assert inline == ["nav.cta"] and hidden == []


def test_a_marker_inside_an_attribute_is_recorded_on_the_element() -> None:
    """`alt="…"` can't hold a wrapper element, so the key rides on the tag."""
    html, inline, hidden = transform(f'<img alt="{mark("home.hero.image_alt", "Un bolso")}">')
    assert html == '<img alt="Un bolso" data-ct-keys="home.hero.image_alt">'
    assert inline == [] and hidden == ["home.hero.image_alt"]


def test_several_attributes_of_one_element_are_all_recorded() -> None:
    html, _inline, hidden = transform(
        f'<a href="{mark("global.instagram_url", "https://x.test")}" '
        f'title="{mark("nav.cta", "Comprar")}">x</a>'
    )
    assert 'data-ct-keys="global.instagram_url nav.cta"' in html
    assert hidden == ["global.instagram_url", "nav.cta"]


def test_a_marker_inside_title_is_stripped_and_recorded() -> None:
    html, inline, hidden = transform(f"<title>{mark('seo.home.title', 'GLÜCK')}</title>")
    assert html == "<title>GLÜCK</title>"
    assert inline == [] and hidden == ["seo.home.title"]


def test_a_marker_inside_a_script_never_becomes_markup() -> None:
    """A wrapper element there would be a syntax error in the page's JavaScript."""
    html, inline, _hidden = transform(f"<script>var a = '{mark('nav.cta', 'Comprar')}';</script>")
    assert html == "<script>var a = 'Comprar';</script>"
    assert inline == []


def test_a_list_item_keeps_its_index() -> None:
    html, inline, _ = transform(
        f"<ul><li>{mark('home.feature.specs#0', 'Uno')}</li>"
        f"<li>{mark('home.feature.specs#1', 'Dos')}</li></ul>"
    )
    assert 'data-k="home.feature.specs#0"' in html
    assert 'data-k="home.feature.specs#1"' in html
    assert inline == ["home.feature.specs", "home.feature.specs"]


def test_the_same_key_in_several_places_is_wrapped_everywhere() -> None:
    html, inline, _ = transform(
        f"<nav>{mark('nav.cta', 'Comprar')}</nav><footer>{mark('nav.cta', 'Comprar')}</footer>"
    )
    assert html.count('<ct-t data-k=\"nav.cta\"') == 2
    assert inline == ["nav.cta", "nav.cta"]


def test_markup_without_markers_is_returned_untouched() -> None:
    original = '<!DOCTYPE html><html><body><p class="x">hola</p><!-- nota --></body></html>'
    html, inline, hidden = transform(original)
    assert html == original
    assert not inline and not hidden


def test_comments_and_doctypes_do_not_confuse_the_scanner() -> None:
    html, inline, _ = transform(
        f"<!-- <p>no soy markup real</p> --><p>{mark('nav.cta', 'Comprar')}</p>"
    )
    assert html.startswith("<!-- <p>no soy markup real</p> -->")
    assert inline == ["nav.cta"]


def test_a_self_closing_tag_keeps_being_self_closing() -> None:
    html, _inline, _hidden = transform(f'<img alt="{mark("nav.cta", "x")}"/>')
    assert html == '<img alt="x" data-ct-keys="nav.cta"/>'


def test_a_truncated_marker_is_dropped_rather_than_rendered() -> None:
    html, inline, hidden = transform(f"<p>{EDIT_START}nav.cta sin cierre</p>")
    assert not any(marker in html for marker in MARKERS)
    assert not inline and not hidden


def test_the_transform_never_leaves_a_marker_behind() -> None:
    html, _inline, _hidden = transform(
        f'<div title="{mark("a.b", "1")}">{mark("c.d", "2")}<script>{mark("e.f", "3")}</script></div>'
    )
    assert not any(marker in html for marker in MARKERS)


# --- end to end through a real page ---------------------------------------------


def test_edit_mode_wraps_the_visible_copy_of_the_home(auth_client: FlaskClient) -> None:
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    assert '<ct-t data-k=\"nav.cta\"' in html
    assert '<ct-t data-k=\"home.hero.eyebrow\"' in html
    assert html.count("<ct-t ") > 40


def test_edit_mode_records_the_invisible_copy_on_its_elements(auth_client: FlaskClient) -> None:
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    assert 'data-ct-keys="home.hero.image_alt"' in html
    manifest = _manifest(html)
    assert "seo.home.title" in manifest["hiddenKeys"]
    assert "home.hero.image_alt" in manifest["hiddenKeys"]


def test_the_manifest_carries_the_raw_value_not_the_rendered_one(
    auth_client: FlaskClient,
) -> None:
    """Typing over an interpolated "{tagline}" would bake it in forever, so the
    editor edits the raw string."""
    manifest = _manifest(auth_client.get("/?edit=1").get_data(as_text=True))
    assert manifest["fields"]["home.hero.title"]["raw"] == "{tagline}"
    assert manifest["tokens"]["tagline"] == registry.DEFAULTS["global.tagline"]


def test_the_manifest_describes_each_field_for_the_panel(auth_client: FlaskClient) -> None:
    manifest = _manifest(auth_client.get("/?edit=1").get_data(as_text=True))
    entry = manifest["fields"]["seo.home.description"]
    assert entry["label"] == registry.FIELDS["seo.home.description"].label
    assert entry["type"] == "text"
    assert entry["max"] == registry.FIELDS["seo.home.description"].max_length
    assert entry["group"] == "seo" and entry["groupTitle"] and entry["section"]


def test_edit_mode_shows_pending_drafts(auth_client: FlaskClient) -> None:
    auth_client.post(
        "/admin/content/save",
        json={"changes": {"home.hero.eyebrow": "Antetítulo borrador"}, "action": "save"},
    )
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    assert "Antetítulo borrador" in html
    assert _manifest(html)["fields"]["home.hero.eyebrow"]["hasDraft"] is True


def test_the_editor_assets_are_injected_only_in_edit_mode(auth_client: FlaskClient) -> None:
    edit = auth_client.get("/?edit=1").get_data(as_text=True)
    assert "js/editor-frame.js" in edit and "css/editor-frame.css" in edit
    normal = auth_client.get("/").get_data(as_text=True)
    assert "editor-frame" not in normal


@pytest.mark.parametrize("path", ["/", "/carrito", "/nosotras", "/categoria/tote", "/gracias"])
def test_edit_mode_works_on_every_kind_of_page(auth_client: FlaskClient, path: str) -> None:
    html = auth_client.get(f"{path}?edit=1").get_data(as_text=True)
    assert "<ct-t " in html, path
    assert _manifest(html)["fields"], path


# --- nothing leaks --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/carrito", "/nosotras", "/categoria/tote"])
def test_a_public_page_never_carries_editor_machinery(client: FlaskClient, path: str) -> None:
    for url in (path, f"{path}?edit=1"):
        html = client.get(url).get_data(as_text=True)
        assert not any(marker in html for marker in MARKERS), url
        assert "<ct-t" not in html, url
        assert "ctManifest" not in html, url


def test_edit_mode_is_ignored_without_an_admin_session(client: FlaskClient) -> None:
    plain = client.get("/").get_data(as_text=True)
    attempted = client.get("/?edit=1").get_data(as_text=True)
    assert "<ct-t" not in attempted
    # Same page either way (only the canonical/og URLs could differ, and they use
    # request.path, so the bodies match exactly).
    assert plain == attempted


def test_edit_mode_is_never_indexable_or_cacheable(auth_client: FlaskClient) -> None:
    response = auth_client.get("/?edit=1")
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" in response.headers["Cache-Control"]


def test_json_payloads_stay_free_of_markers(auth_client: FlaskClient) -> None:
    """The cart strings and the JSON-LD are serialized, so a marker would survive
    there as literal \\uXXXX text instead of being rewritten."""
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    for block in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
        if not block.strip().startswith(("{", "[")):
            continue
        assert "\\ue000" not in block and "\\ue001" not in block
        json.loads(block)


def test_non_html_responses_are_left_alone(auth_client: FlaskClient) -> None:
    for path in ("/sitemap.xml", "/robots.txt"):
        body = auth_client.get(f"{path}?edit=1").get_data(as_text=True)
        assert "<ct-t" not in body and not any(m in body for m in MARKERS), path


# --- regressions found by using the editor for real ---------------------------


def test_the_category_cards_are_editable(auth_client: FlaskClient) -> None:
    """They render through the category helpers, not a t() call, so they used to be
    the one big block of home copy the visual editor could not touch."""
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    assert '<ct-t data-k=\"category.tote.label\"' in html
    assert '<ct-t data-k=\"category.tote.tagline\"' in html
    manifest = _manifest(html)
    for slug in registry.CURATED_CATEGORY_SLUGS:
        assert f"category.{slug}.label" in manifest["inlineKeys"], slug


def test_the_category_page_heading_and_intro_are_editable(auth_client: FlaskClient) -> None:
    html = auth_client.get("/categoria/tote?edit=1").get_data(as_text=True)
    assert '<ct-t data-k=\"category.tote.label\"' in html
    assert '<ct-t data-k=\"category.tote.intro\"' in html


def test_a_category_label_inside_a_meta_tag_stays_a_plain_value(
    auth_client: FlaskClient,
) -> None:
    """The label is ALSO a t() param (the page title). Nesting one marker inside
    another would corrupt the rewrite, so params use the plain variant."""
    html = auth_client.get("/categoria/tote?edit=1").get_data(as_text=True)
    assert "<title>Tote · Carteras de cuero vegano | GLÜCK</title>" in html
    assert not any(marker in html for marker in MARKERS)


def test_an_editable_value_passed_as_a_param_cannot_nest_markers(app) -> None:
    """Belt and braces: even if a template does pass one, the value is stripped."""
    from app import auth
    from app.content import resolver

    with app.test_request_context("/?edit=1"):
        auth.login()
        nested = resolver.editable("nav.cta")
        rendered = str(resolver.editable("category.meta.title", category=nested))
        assert rendered.count(EDIT_START) == 1
        assert rendered.count(EDIT_END) == 1
        assert "Comprar" in rendered


def test_the_brand_is_editable_from_the_wordmark(auth_client: FlaskClient) -> None:
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    assert '<ct-t data-k=\"global.brand\"' in html


def test_the_drawer_and_menu_copy_is_wrapped_even_though_they_start_closed(
    auth_client: FlaskClient,
) -> None:
    """They are in the DOM from the start, so they are editable as soon as the site
    opens them inside the editor."""
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    for key in ("cart.drawer.title", "cart.subtotal_label", "cart.checkout.button"):
        assert f'<ct-t data-k="{key}"' in html, key


# --- scanner robustness, from the code review ---------------------------------


def test_a_stray_marker_does_not_swallow_the_rest_of_the_page() -> None:
    """Unbounded, a single private-use character — an icon-font paste in a Tienda Nube
    product title — consumed kilobytes of markup as if it were a key."""
    html, inline, _hidden = transform(
        f"<h3>Tote{EDIT_START} Negro</h3><span>x</span><h1>{mark('nav.cta', 'Comprar')}</h1>"
    )
    assert not any(m in html for m in MARKERS)
    assert "<h3>Tote Negro</h3><span>x</span>" in html
    assert inline == ["nav.cta"]


def test_an_uppercase_raw_text_close_does_not_disable_the_rest_of_the_page() -> None:
    """`_tag_name` lowercases, so `</SCRIPT>` never matched and everything after it
    silently dropped to panel-only."""
    html, inline, _hidden = transform(
        f"<SCRIPT>var a=1;</SCRIPT><h1>{mark('nav.cta', 'Comprar')}</h1>"
    )
    assert '<ct-t data-k="nav.cta"' in html
    assert inline == ["nav.cta"]


@pytest.mark.parametrize(
    "document",
    [
        "<body><!-- {m} --></body>",
        "<p>a</p><!-- sin cerrar {m} <p>b</p>",
        "<div><!--[if IE]>{m}<![endif]--></div>",
    ],
)
def test_a_marker_inside_a_comment_never_reaches_the_browser(document: str) -> None:
    html, _inline, _hidden = transform(document.format(m=mark("nav.cta", "Comprar")))
    assert not any(m in html for m in MARKERS)
    assert "Comprar" in html


def test_a_value_landing_in_an_attribute_is_escaped() -> None:
    """A rich value carries real markup; unescaped, a `"` broke out of the attribute."""
    html, _inline, hidden = transform(
        f'<meta content="{mark("home.feature.title", chr(34) + "><script>x</script>")}">'
    )
    assert "<script>" not in html
    assert hidden == ["home.feature.title"]
    assert html.count("<meta") == 1


def test_the_manifest_carries_the_previous_wording(auth_client: FlaskClient) -> None:
    """Without it the panel's "Volver al texto anterior" could never appear for a key
    the current page renders — i.e. almost never."""
    auth_client.post(
        "/admin/content/save",
        json={"changes": {"nav.cta": "Uno"}, "action": "publish", "keys": ["nav.cta"]},
    )
    auth_client.post(
        "/admin/content/save",
        json={"changes": {"nav.cta": "Dos"}, "action": "publish", "keys": ["nav.cta"]},
    )
    entry = _manifest(auth_client.get("/?edit=1").get_data(as_text=True))["fields"]["nav.cta"]
    assert entry["previous"] == "Uno"


def test_a_hostile_value_cannot_break_out_of_the_manifest(app, auth_client: FlaskClient) -> None:
    """The manifest carries RAW values, and `line`/`text` fields are never sanitized."""
    auth_client.post(
        "/admin/content/save",
        json={
            "changes": {"home.hero.eyebrow": "</script><script>window.x=1</script>"},
            "action": "publish",
            "keys": ["home.hero.eyebrow"],
        },
    )
    html = auth_client.get("/?edit=1").get_data(as_text=True)
    raw = re.search(r'id="ctManifest">(.*?)</script>', html, re.S)
    assert raw is not None
    assert "<script" not in raw.group(1)
    # …and it is still valid JSON carrying the hostile value as data.
    assert _manifest(html)["fields"]["home.hero.eyebrow"]["raw"].startswith("</script>")


def test_the_cart_copy_reaches_the_editors_manifest(auth_client: FlaskClient) -> None:
    """The drawer's strings ship as JSON for cart.js, so they use the marker-free
    helper — which used to mean the editor never listed them at all."""
    manifest = _manifest(auth_client.get("/?edit=1").get_data(as_text=True))
    for key in ("cart.page.empty", "cart.checkout.error", "cart.line.remove"):
        assert key in manifest["fields"], key


def test_the_editorial_pages_meta_description_is_listed(auth_client: FlaskClient) -> None:
    manifest = _manifest(auth_client.get("/nosotras?edit=1").get_data(as_text=True))
    assert "page.about.meta_description" in manifest["fields"]
