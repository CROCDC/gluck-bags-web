"""Turning an edit-mode page into the visual editor's canvas.

In edit mode every resolved string is emitted wrapped in private-use markers (see
`resolver.editable`). This module runs once per HTML response and rewrites those
markers into something the editor can actually work with:

- a value that landed in **visible text** becomes `<ct-t data-k="key">…</ct-t>`,
  which the in-frame script makes click-to-edit;
- a value that landed **inside an attribute** (`alt`, `aria-label`, `content`, …)
  can't be wrapped, so the marker is stripped and its key is recorded on the owning
  element as `data-ct-keys` — the editor shows a badge that opens the side panel;
- a value inside `<title>`/`<script>`/`<style>`/`<textarea>` is stripped and recorded
  as a page-level field (the SEO panel).

Doing it here, instead of annotating ~200 template call sites, means the editor
automatically covers every string in the registry — including new ones — and the
public render path is completely untouched: markers only exist when a logged-in
admin asks for `?edit=1`.

The scan is a single left-to-right pass with three states (text / inside a tag /
inside a raw-text element). It is not a full HTML parser and does not need to be:
it only has to know whether a marker sits inside a tag, and our own values never
contain a raw `<` or `>` (they are escaped on the way out).
"""

from __future__ import annotations

import json
import os
from typing import Any

from flask import Flask
from markupsafe import escape

from app.content import registry
from app.content.resolver import (
    _strip_markers,
    EDIT_END,
    EDIT_SEP,
    EDIT_START,
    field_state,
    is_edit_mode,
    rendered_keys,
)

# Elements whose content is raw text: a wrapper element inside them would render as
# literal characters (or break a script), so their values are panel-only.
RAWTEXT_TAGS = frozenset({"script", "style", "title", "textarea"})


def _tag_name(tag_html: str) -> str:
    """`<a href=…` -> `a`, `</p` -> `p`, `<!doctype html` -> `!doctype`."""
    body = tag_html[1:].lstrip("/")
    name: list[str] = []
    for char in body:
        if char.isspace() or char in "/>":
            break
        name.append(char)
    return "".join(name).lower()


def _with_keys_attr(tag_html: str, labels: list[str]) -> str:
    """Record on the start tag which fields its attributes came from."""
    attr = f' data-ct-keys="{escape(" ".join(labels))}"'
    if tag_html.endswith("/"):
        return tag_html[:-1] + attr + "/"
    return tag_html + attr


def transform(html: str) -> tuple[str, list[str], list[str]]:
    """Rewrite edit markers. Returns (html, inline keys, panel-only keys)."""
    out: list[str] = []
    inline: list[str] = []
    hidden: list[str] = []

    i = 0
    length = len(html)
    tag_buf: list[str] | None = None  # not None => we are inside <…>
    tag_labels: list[str] = []
    rawtext: str | None = None

    while i < length:
        char = html[i]

        if char == EDIT_START:
            # Bound both searches by the NEXT marker start: unbounded, a single
            # stray private-use character (an icon-font paste in a Tienda Nube
            # product title) consumed kilobytes of page markup as a "key".
            limit = html.find(EDIT_START, i + 1)
            limit = len(html) if limit == -1 else limit
            sep = html.find(EDIT_SEP, i, limit)
            end = html.find(EDIT_END, sep + 1, limit) if sep != -1 else -1
            if sep == -1 or end == -1:
                # Truncated marker (a value cut by a length limit, say): drop the
                # stray character rather than emitting it into the page.
                i += 1
                continue
            label = html[i + 1 : sep]
            value = html[sep + 1 : end]
            key = label.split("#", 1)[0]
            if tag_buf is not None:
                # Escaped: a `rich` value carries real markup, and an unescaped `"`
                # would break out of the attribute it landed in.
                tag_buf.append(str(escape(value)))
                tag_labels.append(label)
                hidden.append(key)
            elif rawtext is not None:
                out.append(value)
                hidden.append(key)
            else:
                # Rich fields hold block elements. The host has to be a block too, or
                # the browser reparents (and loses) what the editor types into it.
                field = registry.FIELDS.get(key)
                kind = f' data-t="{field.type}"' if field is not None else ""
                out.append(f'<ct-t data-k="{escape(label)}"{kind}>{value}</ct-t>')
                inline.append(key)
            i = end + 1
            continue

        if rawtext is not None:
            # `_tag_name` lowercases, so a literal `</SCRIPT>` never matched and the
            # scanner stayed in raw-text mode for the rest of the document.
            if char == "<" and html[i : i + len(rawtext) + 2].lower() == f"</{rawtext}":
                rawtext = None
                tag_buf = [char]
                tag_labels = []
                i += 1
                continue
            out.append(char)
            i += 1
            continue

        if tag_buf is not None:
            if char == ">":
                tag_html = "".join(tag_buf)
                name = _tag_name(tag_html)
                if tag_labels:
                    tag_html = _with_keys_attr(tag_html, tag_labels)
                out.append(tag_html + ">")
                if name in RAWTEXT_TAGS and not tag_html.startswith("</") and not tag_html.endswith("/"):
                    rawtext = name
                tag_buf = None
                tag_labels = []
                i += 1
                continue
            tag_buf.append(char)
            i += 1
            continue

        if char == "<":
            if html.startswith("<!--", i):
                close = html.find("-->", i)
                close = length if close == -1 else close + 3
                # Strip markers rather than copying the comment byte for byte: a
                # marker inside one (or inside an unterminated one, which runs to
                # EOF) reached the browser as private-use tofu.
                out.append(_strip_markers(html[i:close]))
                i = close
                continue
            tag_buf = [char]
            tag_labels = []
            i += 1
            continue

        out.append(char)
        i += 1

    if tag_buf is not None:  # unterminated tag: emit what we buffered
        out.append("".join(tag_buf))

    return "".join(out), inline, hidden


def build_manifest(path: str, inline: list[str], hidden: list[str]) -> dict[str, Any]:
    """Everything the editor needs about the strings on this page.

    Values are carried RAW (with their `{tokens}` intact) alongside the rendered
    text: inline editing has to write the raw value back, or typing over
    `{brand}` would silently bake the brand name into that string forever.
    """
    from app.content.resolver import _global_tokens

    fields: dict[str, Any] = {}
    for key in list(registry.TOKEN_FIELDS) + rendered_keys():
        field = registry.FIELDS.get(key)
        if field is None:
            continue
        state = field_state(key)
        fields[key] = {
            "raw": state["value"],
            "type": field.type,
            "label": field.label,
            "hint": field.hint,
            "max": field.max_length,
            "default": field.default,
            # Without this the panel's "Volver al texto anterior" could never appear
            # for a key the current page renders — i.e. almost never.
            "previous": state["previous"],
            "group": registry.FIELD_GROUP[key],
            "groupTitle": registry.GROUPS_BY_KEY[registry.FIELD_GROUP[key]].title,
            "section": registry.FIELD_SECTION.get(key, ""),
            "hasDraft": state["has_draft"],
            "isOverridden": state["is_overridden"],
        }
    inline_set = list(dict.fromkeys(inline))
    inline_only = set(inline_set)
    hidden_keys = [k for k in dict.fromkeys(list(registry.TOKEN_FIELDS) + hidden) if k not in inline_only]
    return {
        "path": path,
        "tokens": _global_tokens(),
        "fields": fields,
        "inlineKeys": inline_set,
        "hiddenKeys": hidden_keys,
    }


def _asset(app: Flask, filename: str) -> str:
    """A `/static/...` URL with the same mtime cache-buster the app uses elsewhere."""
    url = f"/static/{filename}"
    try:
        mtime = int(os.stat(os.path.join(app.static_folder or "", filename)).st_mtime)
    except OSError:
        return url
    return f"{url}?v={mtime}"


def _payload(app: Flask, manifest: dict[str, Any]) -> str:
    # Same escaping as the JSON-LD block: json.dumps leaves '<' alone, which would
    # let a value containing '</script>' break out of the element.
    data = (
        json.dumps(manifest, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        f'<script type="application/json" id="ctManifest">{data}</script>'
        f'<link rel="stylesheet" href="{_asset(app, "css/editor-frame.css")}">'
        f'<script defer src="{_asset(app, "js/editor-frame.js")}"></script>'
    )


def install(app: Flask) -> None:
    """Rewrite edit-mode responses. Registered after Compress so it runs first."""

    @app.after_request
    def _apply_editor_markup(response: Any) -> Any:
        if not is_edit_mode():
            return response
        if response.direct_passthrough or response.mimetype != "text/html":
            return response
        html = response.get_data(as_text=True)
        if EDIT_START not in html:
            return response
        html, inline, hidden = transform(html)
        manifest = build_manifest(_request_path(), inline, hidden)
        payload = _payload(app, manifest)
        if "</body>" in html:
            html = html.replace("</body>", payload + "</body>", 1)
        else:
            html += payload
        response.set_data(html)
        return response


def _request_path() -> str:
    from flask import request

    return request.path
