"""Resolving a registry key into the string a page renders.

Precedence, lowest to highest: the registry default -> the published override ->
(preview mode only) the pending draft. Templates never see that machinery; they
call `t('home.hero.title')`.

Two properties this module exists to guarantee:

1. **A content lookup can never break the site.** A missing key, an unreachable
   database or a half-written draft degrades to the registry default. In debug/test
   an unknown key raises instead, so a typo fails loudly in CI rather than silently
   rendering an empty heading in production.
2. **Rich values are sanitized on the way OUT, not only on the way in.** The admin
   sanitizes on save; doing it again here means a value written straight into the
   database (a restored backup, a manual UPDATE) still can't inject script into a
   public page.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from flask import Flask, g, has_app_context, has_request_context, request
from markupsafe import Markup

from app.content import registry
from app.content.sanitizer import safe_href, sanitize

# `?preview=1` on any public URL renders pending drafts — for a logged-in admin only.
PREVIEW_PARAM = "preview"
# `?edit=1` additionally turns the page into the visual editor's canvas.
EDIT_PARAM = "edit"

_CACHE_KEY = "_content_cache"
_TOKEN_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


# --- per-request state --------------------------------------------------------


def _cache() -> dict[str, Any]:
    """Scratch space for this request (falls back to a throwaway dict off-context).

    The overrides are read once per request rather than cached in the process: the
    app runs several gunicorn workers over one SQLite file, so a process-level cache
    would keep serving stale copy in the other workers after an edit. It is one
    small SELECT against a table with one row per overridden string.
    """
    if not has_app_context():
        return {}
    cache = getattr(g, _CACHE_KEY, None)
    if cache is None:
        cache = {}
        setattr(g, _CACHE_KEY, cache)
    return cache


def _overrides() -> dict[str, tuple[str | None, str | None]]:
    cache = _cache()
    if "overrides" in cache:
        return cache["overrides"]
    try:
        from app.repositories import SiteTextRepository

        overrides = SiteTextRepository.as_map()
    except Exception:  # noqa: BLE001 — copy must never take the site down
        overrides = {}
    cache["overrides"] = overrides
    return overrides


def invalidate() -> None:
    """Drop this context's snapshot of the overrides.

    Called after every write (see SiteTextRepository.save): `g` lives for the whole
    app context, so without this an admin request that saves and then re-reads —
    or a test that does both inside one context — would keep serving the values
    from before the write.
    """
    cache = _cache()
    cache.pop("overrides", None)
    cache.pop("tokens", None)
    cache.pop("previous", None)


def _admin_flag(param: str, cache_key: str) -> bool:
    """True when `?<param>=1` is set AND the request carries an admin session.

    The session check is what keeps unpublished copy — and the editing machinery —
    from ever reaching the public through a shared link.
    """
    cache = _cache()
    if cache_key in cache:
        return cache[cache_key]
    active = False
    if has_request_context() and request.args.get(param):
        from app import auth

        active = auth.is_logged_in()
    cache[cache_key] = active
    return active


def is_preview() -> bool:
    """True when this request should render pending drafts instead of live copy."""
    return _admin_flag(PREVIEW_PARAM, "preview") or is_edit_mode()


def is_edit_mode() -> bool:
    """True when the page is being rendered inside the visual editor.

    Edit mode implies preview (you edit on top of your pending drafts) and makes
    every resolved string carry its key, so the editor can map a click on the page
    back to the field it came from. See app/content/editor_markup.py.
    """
    return _admin_flag(EDIT_PARAM, "edit")


# --- resolution ---------------------------------------------------------------


def effective(key: str) -> str:
    """The stored text for `key` before token interpolation."""
    default = registry.DEFAULTS.get(key, "")
    published, draft = _overrides().get(key, (None, None))
    if draft is not None and is_preview():
        return draft
    return published if published is not None else default


def _interpolate(value: str, tokens: dict[str, str]) -> str:
    """Replace `{token}` with its value; unknown tokens are left untouched.

    Deliberately not str.format: an editor typing a stray brace must never raise
    (KeyError/ValueError) in the middle of rendering a public page.
    """
    if "{" not in value:
        return value
    return _TOKEN_RE.sub(lambda m: tokens.get(m.group(1), m.group(0)), value)


def _global_tokens() -> dict[str, str]:
    """The tokens available to every string ({brand}, {tagline}, {year}, …).

    Resolved without recursing back through `t()`: brand first, then the values that
    may themselves mention {brand}.
    """
    cache = _cache()
    tokens = cache.get("tokens")
    if tokens is not None:
        return tokens
    tokens = {"brand": effective("global.brand"), "year": str(datetime.now().year)}
    tokens["tagline"] = _interpolate(effective("global.tagline"), tokens)
    # The Instagram URL is rendered straight into href="…" all over the site, so it
    # goes through the same link guard as any `url` field (see `t`).
    tokens["instagram_url"] = _safe_url(
        _interpolate(effective("global.instagram_url"), tokens), "global.instagram_url"
    )
    tokens["instagram_handle"] = _interpolate(effective("global.instagram_handle"), tokens)
    cache["tokens"] = tokens
    return tokens


def _missing(key: str) -> str:
    from flask import current_app

    if has_app_context() and (current_app.debug or current_app.testing):
        raise KeyError(f"Unknown site-text key: {key!r} (see app/content/registry.py)")
    if has_app_context():
        current_app.logger.warning("Unknown site-text key rendered as empty: %r", key)
    return ""


def t(key: str, **params: Any) -> str | Markup:
    """The text for `key`, ready to render.

    `params` fill per-call tokens, e.g. ``t("category.meta.title", category="Tote")``.
    Rich fields come back as sanitized Markup; every other type comes back as a plain
    string that Jinja escapes as usual.
    """
    field = registry.FIELDS.get(key)
    if field is None:
        return _missing(key)
    tokens = dict(_global_tokens())
    if params:
        # A param is data. Strip any edit markers it carries (a template passing an
        # already-editable value through), because nesting them would corrupt the
        # markup the response hook produces.
        tokens.update(
            {name: _strip_markers("" if value is None else str(value)) for name, value in params.items()}
        )
    value = _interpolate(effective(key), tokens)
    if field.type == "rich":
        # Interpolate BEFORE sanitizing: a token's value is data, so it gets escaped
        # like any other text instead of being spliced in as live markup.
        return Markup(sanitize(value))
    if field.type == "url":
        return _safe_url(value, key)
    return value


def _safe_url(value: str, key: str) -> str:
    """A link we are willing to put in an href, else the registry default.

    Re-checked at render, not only in the admin form: a value that reached the table
    some other way (a restored backup, a manual UPDATE) must not be able to turn
    every Instagram link on the site into `javascript:`.
    """
    if safe_href(value) and value.lower().startswith(("http://", "https://")):
        return value
    return registry.DEFAULTS.get(key, "")


def t_lines(key: str, **params: Any) -> list[str]:
    """A `lines` field as a list (blank lines dropped)."""
    value = t(key, **params)
    return [line.strip() for line in str(value).splitlines() if line.strip()]


# --- edit mode ----------------------------------------------------------------
#
# In the visual editor every rendered string has to be traceable back to its key.
# Rather than annotating ~200 template call sites by hand, edit mode wraps each
# resolved value in a marker that a response hook turns into real markup (or, for
# values that landed inside an attribute or a <title>, records on the element).
# The markers use private-use codepoints so they can never collide with copy.

EDIT_START = "\ue000"
EDIT_SEP = "\ue001"
EDIT_END = "\ue002"
EDIT_MARKERS = (EDIT_START, EDIT_SEP, EDIT_END)


def _strip_markers(value: str) -> str:
    for marker in EDIT_MARKERS:
        value = value.replace(marker, "")
    return value


def _record_rendered(key: str) -> None:
    """Remember that `key` was rendered here, so the editor's manifest can carry it."""
    cache = _cache()
    cache.setdefault("rendered_keys", []).append(key)


def rendered_keys() -> list[str]:
    """Keys rendered during this request, in first-seen order."""
    seen: dict[str, None] = {}
    for key in _cache().get("rendered_keys", []):
        seen.setdefault(key, None)
    return list(seen)


def _wrap(key: str, value: str | Markup, line: int | None = None) -> Markup:
    """Tag `value` with its key for the editor.

    Concatenating onto a Markup escapes a plain `value` exactly like Jinja would, so
    edit mode can never turn a non-rich field into live markup.
    """
    _record_rendered(key)
    label = key if line is None else f"{key}#{line}"
    return Markup(f"{EDIT_START}{label}{EDIT_SEP}") + value + Markup(EDIT_END)


def editable(key: str, **params: Any) -> str | Markup:
    """`t()` for templates: identical output, plus the editor tag in edit mode."""
    value = t(key, **params)
    if not is_edit_mode() or key not in registry.FIELDS:
        return value
    return _wrap(key, value)


def editable_lines(key: str, **params: Any) -> list[str] | list[Markup]:
    """`t_lines()` for templates. Each item carries its index so the editor can put
    an edited line back in the right place."""
    items = t_lines(key, **params)
    if not is_edit_mode() or key not in registry.FIELDS:
        return items
    return [_wrap(key, item, line=index) for index, item in enumerate(items)]


# --- convenience for routes / services ----------------------------------------


def brand() -> str:
    return _global_tokens()["brand"]


def tagline() -> str:
    return _global_tokens()["tagline"]


def instagram_url() -> str:
    return _global_tokens()["instagram_url"]


def category_label(name: str) -> str:
    """The display label of a curated category (its canonical name otherwise).

    The canonical name is identity — it is the URL slug and the value stored on each
    product — so it is not editable. This is the layer that lets the shop rename
    "Mini Bag" on screen without moving /categoria/mini-bag.
    """
    if not name:
        return ""
    from app.utils import slugify

    key = f"category.{slugify(name)}.label"
    if key in registry.FIELDS:
        return str(t(key))
    return name


def category_tagline(name: str) -> str:
    from app.utils import slugify

    key = f"category.{slugify(name)}.tagline"
    return str(t(key)) if key in registry.FIELDS else ""


def category_intro(name: str) -> str | None:
    from app.utils import slugify

    key = f"category.{slugify(name)}.intro"
    if key not in registry.FIELDS:
        return None
    value = str(t(key)).strip()
    return value or None


def _category_key(name: str, suffix: str) -> str | None:
    if not name:
        return None
    from app.utils import slugify

    key = f"category.{slugify(name)}.{suffix}"
    return key if key in registry.FIELDS else None


def category_label_editable(name: str) -> str | Markup:
    """The category label as RENDERED copy (click-to-edit in the visual editor).

    The pure `category_label()` above stays the one to use for logic and for `t()`
    params — an editable value nested inside another value is not markup we can
    rewrite.
    """
    key = _category_key(name, "label")
    return editable(key) if key else (name or "")


def category_tagline_editable(name: str) -> str | Markup:
    key = _category_key(name, "tagline")
    return editable(key) if key else ""


def category_intro_editable(name: str) -> str | Markup | None:
    key = _category_key(name, "intro")
    if key is None:
        return None
    value = editable(key)
    return value if str(value).strip() else None


# --- admin-facing state -------------------------------------------------------


def field_state(key: str) -> dict[str, Any]:
    """Everything the editor needs about one field, in one dict."""
    field = registry.FIELDS[key]
    published, draft = _overrides().get(key, (None, None))
    live = published if published is not None else field.default
    previous = _previous_values().get(key)
    return {
        "field": field,
        "default": field.default,
        "published": published,
        "draft": draft,
        "live": live,
        # What the textarea shows: the pending draft, else what is live.
        "value": draft if draft is not None else live,
        "has_draft": draft is not None,
        "is_overridden": published is not None,
        "returns_to_default": draft is not None and draft == field.default,
        # What was live before the last publish, so a mistake has a way back.
        "previous": previous,
        "has_previous": previous is not None and previous != live,
    }


def _previous_values() -> dict[str, str]:
    """`key -> the value that was live before the last publish`, once per request."""
    cache = _cache()
    if "previous" in cache:
        return cache["previous"]
    try:
        from app.repositories import SiteTextRepository

        values = {
            row.key: row.previous_value
            for row in SiteTextRepository.get_all()
            if row.previous_value is not None
        }
    except Exception:  # noqa: BLE001 — never let this break a page
        values = {}
    cache["previous"] = values
    return values


def group_states(group: registry.Group) -> dict[str, dict[str, Any]]:
    return {f.key: field_state(f.key) for f in group.fields}


def pending_draft_count(group: registry.Group | None = None) -> int:
    keys = (
        [f.key for f in group.fields] if group is not None else list(registry.FIELDS.keys())
    )
    overrides = _overrides()
    return sum(1 for key in keys if overrides.get(key, (None, None))[1] is not None)


def override_count(group: registry.Group) -> int:
    overrides = _overrides()
    return sum(
        1 for f in group.fields if overrides.get(f.key, (None, None))[0] is not None
    )


# --- wiring -------------------------------------------------------------------


def register_content(app: Flask) -> None:
    """Expose the resolver to Jinja and harden preview responses."""

    # Templates get the edit-aware variants: identical output on a normal request,
    # key-tagged inside the visual editor. `t_plain` is the escape hatch for values
    # that are serialized (JSON) instead of rendered, where a marker would survive
    # as literal text.
    app.jinja_env.globals["t"] = editable
    app.jinja_env.globals["t_lines"] = editable_lines
    app.jinja_env.globals["t_plain"] = t
    # `category_label` renders (editable); `category_name` is the plain string for
    # logic and for t() params.
    app.jinja_env.globals["category_label"] = category_label_editable
    app.jinja_env.globals["category_name"] = category_label
    app.jinja_env.globals["content_preview"] = is_preview

    from app.content import editor_markup

    editor_markup.install(app)

    @app.after_request
    def _mark_preview(response: Any) -> Any:
        # A preview renders unpublished copy: keep it out of the index and out of
        # every cache (the CDN in front of the site included).
        if is_preview():
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["Cache-Control"] = "no-store, private"
        return response
