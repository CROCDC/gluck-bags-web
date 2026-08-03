"""Allow-list HTML sanitizer for the `rich` site-text fields.

The editorial/legal pages are written as small HTML fragments (headings, lists,
links), so those fields can't be plain escaped text. Anything an editor can store
is HTML that ends up inside a public page, which makes it a stored-XSS surface the
moment the admin password leaks — so the value is reduced to a fixed allow-list of
tags and attributes instead of being trusted.

Standard library only (no bleach): the grammar we accept is tiny, and a dependency
that ships a parser is a bigger surface than the ~120 lines below.

Design notes:
- Unknown tags are dropped but their TEXT is kept, so a paste from a word processor
  degrades to readable copy instead of disappearing.
- `script`/`style`/`iframe`/… are dropped WITH their content: that content is code,
  not copy.
- Unbalanced markup is repaired (auto-close on end tag, close the stack at EOF), so
  a half-typed tag can never break the page layout.
- `sanitize()` is idempotent: sanitizing an already-sanitized value is a no-op.
  Callers rely on that to sanitize both on save and on render.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

# tag -> attributes kept on it. Everything else is stripped.
ALLOWED_TAGS: dict[str, frozenset[str]] = {
    "p": frozenset(),
    "br": frozenset(),
    "strong": frozenset(),
    "b": frozenset(),
    "em": frozenset(),
    "i": frozenset(),
    "h2": frozenset(),
    "h3": frozenset(),
    "ul": frozenset(),
    "ol": frozenset(),
    "li": frozenset(),
    "a": frozenset({"href", "target", "rel"}),
}

# Tags with no closing tag and no children.
VOID_TAGS: frozenset[str] = frozenset({"br"})

# Dropped together with their text content (it's code/markup, not copy).
DROP_WITH_CONTENT: frozenset[str] = frozenset(
    {"script", "style", "iframe", "object", "embed", "template", "svg", "math", "noscript"}
)

SAFE_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "tel"})


def safe_href(raw: str | None) -> str | None:
    """Return `raw` if it is a link we're willing to emit, else None.

    Accepts absolute http(s)/mailto/tel, root-relative paths and in-page anchors.
    Rejects `javascript:`, `data:` and friends — including obfuscated forms, since
    the scheme is read up to the first ':' after stripping control characters and
    whitespace (`java\\tscript:` collapses to `javascript:` in some parsers)."""
    if raw is None:
        return None
    value = "".join(ch for ch in raw if ch.isprintable() and not ch.isspace())
    if not value:
        return None
    if value.startswith(("/", "#", "?")):
        return value
    head, sep, _rest = value.partition(":")
    if not sep:
        return value  # relative path ("nosotras", "img/x.png")
    if "/" in head or "#" in head or "?" in head:
        return value  # the ':' is inside the path, not a scheme
    return value if head.lower() in SAFE_SCHEMES else None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs=True: entities arrive already decoded in handle_data, and
        # we re-escape them on the way out — which is what makes sanitize idempotent.
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        # (tag, emitted) — `emitted` is False for tags we swallowed but must still
        # match an end tag for (e.g. an <a> whose href was rejected).
        self._stack: list[tuple[str, bool]] = []
        self._suppress_depth = 0

    # --- helpers ---

    def _attrs_for(self, tag: str, attrs: list[tuple[str, str | None]]) -> str | None:
        """Serialize the kept attributes, or None if the tag must be dropped."""
        allowed = ALLOWED_TAGS[tag]
        kept: dict[str, str] = {}
        for name, value in attrs:
            name = name.lower()
            if name not in allowed:
                continue
            kept[name] = value or ""

        if tag == "a":
            href = safe_href(kept.get("href"))
            if href is None:
                return None  # an <a> we can't link is not worth emitting
            kept["href"] = href
            if kept.get("target"):
                # Only _blank is meaningful here, and it must not hand window.opener
                # to the destination.
                kept["target"] = "_blank"
                rel = set((kept.get("rel") or "").split())
                rel.update({"noopener", "noreferrer"})
                kept["rel"] = " ".join(sorted(rel))
            else:
                kept.pop("target", None)
                kept.pop("rel", None)

        return "".join(f' {name}="{escape(value, quote=True)}"' for name, value in kept.items())

    # --- HTMLParser hooks ---

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._suppress_depth:
            if tag in DROP_WITH_CONTENT:
                self._suppress_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self._suppress_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            return  # drop the tag, keep the text inside it
        serialized = self._attrs_for(tag, attrs)
        if serialized is None:
            if tag not in VOID_TAGS:
                self._stack.append((tag, False))
            return
        self._out.append(f"<{tag}{serialized}>")
        if tag not in VOID_TAGS:
            self._stack.append((tag, True))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._suppress_depth or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self._out.append(f"<{tag}>")
            return
        serialized = self._attrs_for(tag, attrs)
        if serialized is not None:
            self._out.append(f"<{tag}{serialized}></{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._suppress_depth:
            if tag in DROP_WITH_CONTENT:
                self._suppress_depth -= 1
            return
        if tag in VOID_TAGS or tag not in ALLOWED_TAGS:
            return
        # Close everything opened after `tag` too, so unbalanced input can't leak an
        # open element into the rest of the page.
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] != tag:
                continue
            for open_tag, emitted in reversed(self._stack[index:]):
                if emitted:
                    self._out.append(f"</{open_tag}>")
            del self._stack[index:]
            return

    def handle_data(self, data: str) -> None:
        if not self._suppress_depth:
            self._out.append(escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def result(self) -> str:
        for open_tag, emitted in reversed(self._stack):
            if emitted:
                self._out.append(f"</{open_tag}>")
        self._stack.clear()
        return "".join(self._out)


def sanitize(value: str) -> str:
    """Reduce `value` to the allow-listed subset of HTML. Never raises."""
    parser = _Sanitizer()
    parser.feed(value or "")
    parser.close()
    return parser.result()


def strip_tags(value: str) -> str:
    """Plain-text projection of a rich value (for char counts and SERP previews)."""
    parser = _Sanitizer()
    parser.feed(value or "")
    parser.close()
    text = parser.result()
    plain = _TagStripper()
    plain.feed(text)
    plain.close()
    return " ".join("".join(plain.chunks).split())


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Block-level tags become whitespace so words don't run together.
        if tag in ("br", "p", "li", "h2", "h3", "ul", "ol"):
            self.chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "li", "h2", "h3", "ul", "ol"):
            self.chunks.append(" ")
