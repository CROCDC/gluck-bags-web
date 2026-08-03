"""The property everything else rests on: an empty database renders the shipped copy.

Every other test in the content suite asserts a *change* — write an override, see it
on the page. None of them pinned the starting point, so the registry defaults could be
edited (a typo, a bad find-and-replace, a merge) and the whole suite stayed green while
the live site quietly said something else.

This is a snapshot of the visible text of every public page, rendered against a fresh
database. It is deliberately blunt: if you meant to change the copy, regenerate it and
the diff in the review shows exactly which words moved.

    python3 -m pytest tests/test_public_copy_golden.py --snapshot-update

The snapshot holds TEXT, not markup, so restyling a page does not touch it — only the
words do. Product data is excluded by rendering with no catalogue (`SEED_PRODUCTS=0`),
which also keeps it stable across machines.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask.testing import FlaskClient

GOLDEN_DIR = Path(__file__).parent / "golden"

# One entry per kind of public page. The product detail page is absent on purpose:
# it needs catalogue data, which is not copy and not stable.
PAGES: list[tuple[str, str]] = [
    ("home", "/"),
    ("carrito", "/carrito"),
    ("gracias", "/gracias"),
    ("categoria", "/categoria/tote"),
    ("nosotras", "/nosotras"),
    ("contacto", "/contacto"),
    ("envios", "/envios"),
    ("cambios", "/cambios-y-devoluciones"),
    ("terminos", "/terminos"),
    ("privacidad", "/privacidad"),
    ("404", "/una-url-que-no-existe"),
]


class _Visible(HTMLParser):
    """The words a visitor reads: text nodes, plus the attributes that are copy."""

    SKIP = {"script", "style"}
    COPY_ATTRS = ("alt", "aria-label", "placeholder", "title", "content")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
            return
        for name, value in attrs:
            if name in self.COPY_ATTRS and value and value.strip():
                self.chunks.append(f"[{name}] {value.strip()}")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = " ".join(data.split())
        if text:
            self.chunks.append(text)


def _visible_copy(html: str) -> str:
    parser = _Visible()
    parser.feed(html)
    parser.close()
    # Deduplicate consecutive repeats (the marquee prints its phrases twice) so the
    # snapshot stays about the words, not about how often the layout repeats them.
    lines: list[str] = []
    for chunk in parser.chunks:
        if not lines or lines[-1] != chunk:
            lines.append(chunk)
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("name,path", PAGES, ids=[name for name, _ in PAGES])
def test_public_copy_matches_the_shipped_defaults(
    client: FlaskClient, request: pytest.FixtureRequest, name: str, path: str
) -> None:
    response = client.get(path)
    assert response.status_code in (200, 404), path
    current = _visible_copy(response.get_data(as_text=True))

    GOLDEN_DIR.mkdir(exist_ok=True)
    snapshot = GOLDEN_DIR / f"{name}.txt"

    if request.config.getoption("--snapshot-update"):
        snapshot.write_text(current, encoding="utf-8")
        pytest.skip(f"snapshot regenerado: {snapshot.name}")

    assert snapshot.exists(), (
        f"falta {snapshot}. Generalo con:\n"
        f"    python3 -m pytest tests/test_public_copy_golden.py --snapshot-update"
    )
    expected = snapshot.read_text(encoding="utf-8")
    assert current == expected, (
        f"la copy de {path} cambió respecto de lo que dice el registro.\n"
        f"Si el cambio es intencional, regenerá el snapshot:\n"
        f"    python3 -m pytest tests/test_public_copy_golden.py --snapshot-update"
    )


def test_the_snapshot_actually_covers_the_registry(client: FlaskClient) -> None:
    """A snapshot that captured nothing would pass forever. Assert it holds a
    meaningful share of the copy the registry declares."""
    from app.content import registry

    seen = "\n".join(
        _visible_copy(client.get(path).get_data(as_text=True)) for _name, path in PAGES
    )
    line_defaults = [
        field.default
        for field in registry.FIELDS.values()
        if field.type == "line" and "{" not in field.default and len(field.default) > 3
    ]
    found = [default for default in line_defaults if default in seen]
    assert len(found) > len(line_defaults) * 0.5, (
        f"el snapshot sólo cubre {len(found)} de {len(line_defaults)} textos simples"
    )
