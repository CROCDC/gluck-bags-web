"""Structural guards on the editable-copy registry.

The registry is what makes this feature scale: a new string is one entry plus one
`t()` call. These tests are the contract that keeps that cheap — they fail when a
key is duplicated, a template asks for a key nobody declared, a declared key is
dead, or a default would be mangled the first time someone opens the editor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.content import registry
from app.content.sanitizer import sanitize

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "app" / "templates"
APP_PY = REPO_ROOT / "app"

# `t('key')` / `t_lines("key")` as written in templates and Python. The trailing
# `[,)]` is what keeps a runtime-built key (`t('page.' ~ key ~ '.body')`) out of the
# literal set — those are covered by the `dynamic` allow-list below instead.
_KEY_CALL = re.compile(r"""\bt(?:_lines|_plain)?\(\s*(['"])([a-z0-9_.\-]+)\1\s*[,)]""")


def _template_sources() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def _python_sources() -> list[Path]:
    return [p for p in APP_PY.rglob("*.py") if "content/registry.py" not in p.as_posix()]


def _referenced_keys() -> dict[str, list[str]]:
    """Every literal key referenced from a template or from app code -> where."""
    found: dict[str, list[str]] = {}
    for path in _template_sources() + _python_sources():
        text = path.read_text(encoding="utf-8")
        for _quote, key in _KEY_CALL.findall(text):
            found.setdefault(key, []).append(str(path.relative_to(REPO_ROOT)))
    return found


# --- shape ---------------------------------------------------------------------


def test_keys_are_unique_across_the_whole_registry() -> None:
    """Keys are the DB primary key; a duplicate would make two fields fight over one row."""
    seen: dict[str, str] = {}
    for group in registry.GROUPS:
        for field in group.fields:
            assert field.key not in seen, (
                f"{field.key!r} declared twice: in {seen[field.key]!r} and {group.key!r}"
            )
            seen[field.key] = group.key
    assert len(seen) == len(registry.FIELDS)


def test_group_and_section_keys_are_unique() -> None:
    group_keys = [g.key for g in registry.GROUPS]
    assert len(group_keys) == len(set(group_keys))
    for group in registry.GROUPS:
        section_keys = [s.key for s in group.sections]
        assert len(section_keys) == len(set(section_keys)), group.key


def test_every_field_is_usable() -> None:
    for key, field in registry.FIELDS.items():
        assert field.key == key
        assert field.label.strip(), key
        assert field.default.strip(), f"{key} has an empty default"
        assert field.type in ("line", "text", "lines", "rich", "url"), key
        assert field.max_length >= len(field.default), (
            f"{key}: the default ({len(field.default)} chars) does not fit its own "
            f"max_length ({field.max_length})"
        )


def test_single_line_fields_have_no_newlines() -> None:
    for key, field in registry.FIELDS.items():
        if field.type in ("line", "url"):
            assert "\n" not in field.default, key


def test_url_fields_default_to_a_real_link() -> None:
    for key, field in registry.FIELDS.items():
        if field.type == "url":
            assert field.default.startswith("https://"), key


def test_rich_defaults_already_pass_the_sanitizer() -> None:
    """Otherwise the site would silently change the first time someone saves a page
    without editing it (the editor posts the sanitized value back)."""
    for key, field in registry.FIELDS.items():
        if field.type == "rich":
            assert sanitize(field.default) == field.default, key


def test_no_field_ships_placeholder_copy() -> None:
    for key, field in registry.FIELDS.items():
        low = field.default.lower()
        for marker in ("lorem ipsum", "a completar", "todo:", "tbd", "xxx"):
            assert marker not in low, f"{key} ships placeholder copy ({marker!r})"


# --- groups --------------------------------------------------------------------


def test_every_group_has_sections_and_fields() -> None:
    for group in registry.GROUPS:
        assert group.sections, group.key
        assert group.fields, group.key
        assert group.title.strip() and group.description.strip(), group.key
        assert group.preview_kind in ("static", "product", "category"), group.key


def test_static_preview_paths_are_real_urls(app) -> None:
    """A preview target that 404s is a broken screen, so pin it to the url map."""
    client = app.test_client()
    for group in registry.GROUPS:
        if group.preview_kind != "static":
            continue
        response = client.get(group.preview_path)
        expected = 404 if group.key == "errors" else 200
        assert response.status_code == expected, f"{group.key} -> {group.preview_path}"


def test_curated_category_slugs_all_have_their_fields() -> None:
    for slug in registry.CURATED_CATEGORY_SLUGS:
        for suffix in ("label", "tagline", "intro"):
            assert f"category.{slug}.{suffix}" in registry.FIELDS, slug


def test_curated_category_slugs_match_the_routed_categories() -> None:
    from app.routes import CURATED_SLUGS

    assert list(registry.CURATED_CATEGORY_SLUGS) == CURATED_SLUGS


def test_every_editorial_page_route_has_its_fields() -> None:
    from app.routes import STATIC_PAGES

    for page_key in STATIC_PAGES.values():
        for suffix in ("title", "meta_description", "body"):
            assert f"page.{page_key}.{suffix}" in registry.FIELDS, page_key


# --- the registry and the code agree -------------------------------------------


def test_no_template_or_route_asks_for_an_unknown_key() -> None:
    """A typo in a `t()` call must fail here, not render an empty heading in prod."""
    unknown = {
        key: where for key, where in _referenced_keys().items() if key not in registry.FIELDS
    }
    assert not unknown, f"keys referenced but not declared in the registry: {unknown}"


def test_no_declared_key_is_dead() -> None:
    """Every declared field is actually rendered somewhere — otherwise the editor
    shows a text box that changes nothing."""
    referenced = set(_referenced_keys())
    # Resolved dynamically (built from a slug/page key at runtime), so a literal
    # never appears in the source.
    dynamic = {
        f"category.{slug}.{suffix}"
        for slug in registry.CURATED_CATEGORY_SLUGS
        for suffix in ("label", "tagline", "intro")
    }
    from app.routes import STATIC_PAGES

    dynamic |= {
        f"page.{page_key}.{suffix}"
        for page_key in STATIC_PAGES.values()
        for suffix in ("title", "meta_description", "body")
    }
    # Reached through the resolver's own helpers rather than a t() call site.
    dynamic |= {"global.brand", "global.tagline", "global.instagram_url", "global.instagram_handle"}
    dynamic |= {f"home.manifesto.value{n}_{part}" for n in (1, 2, 3) for part in ("title", "body")}

    dead = sorted(set(registry.FIELDS) - referenced - dynamic)
    assert not dead, f"declared but never rendered: {dead}"


@pytest.mark.parametrize("group_key", [g.key for g in registry.GROUPS])
def test_group_lookup_round_trips(group_key: str) -> None:
    group = registry.group_for(group_key)
    assert group is not None and group.key == group_key
    for field in group.fields:
        assert registry.FIELD_GROUP[field.key] == group_key
        assert registry.field_for(field.key) is field


def test_no_group_key_collides_with_a_reserved_admin_route() -> None:
    """`/admin/content/<group_key>` shares its prefix with the publish/discard
    endpoints, so a group named "publish" would shadow one of them."""
    reserved = {"publish", "discard", "preview", "list", "save"}
    assert reserved.isdisjoint({group.key for group in registry.GROUPS})
