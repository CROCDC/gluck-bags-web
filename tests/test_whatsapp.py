"""The floating WhatsApp button and the message it pre-writes.

Two properties matter here and neither is visible from a passing render:

1. **The link is a working wa.me link.** The number is editable copy, so it arrives
   however the shop owner typed it (+, spaces, hyphens, the Argentine mobile 9).
   wa.me only accepts digits, so the href has to normalize it — and has to disappear
   entirely when the number is emptied, instead of linking to a wa.me error page.
2. **The message carries the page's context.** The whole site opens the chat with the
   default message; a product page opens it naming the model and linking its page, so
   the consult arrives with something to answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app import content
from app.repositories import ProductRepository

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient


PUBLIC_PATHS = ["/", "/carrito", "/categoria/tote", "/contacto", "/nosotras", "/terminos"]


def _fab_href(html: str) -> str | None:
    """The href of the floating button, or None when the page doesn't render one."""
    marker = '<a class="wa-fab" href="'
    start = html.find(marker)
    if start == -1:
        return None
    start += len(marker)
    return html[start : html.index('"', start)]


def _message(href: str) -> str:
    """The text wa.me will drop into the chat."""
    return parse_qs(urlsplit(href).query).get("text", [""])[0]


def _publish(app: "Flask", key: str, value: str) -> None:
    with app.test_request_context("/"):
        from sitecopy import current_store, save

        current_store().set_published(key, value)
        save()


# --- the button is on every public page --------------------------------------


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_every_public_page_carries_the_button(client: "FlaskClient", path: str) -> None:
    href = _fab_href(client.get(path).get_data(as_text=True))
    assert href is not None, f"{path} renders no WhatsApp button"
    assert href.startswith("https://wa.me/5491136053910?text="), href


def test_the_default_message_is_the_editable_one(client: "FlaskClient") -> None:
    href = _fab_href(client.get("/").get_data(as_text=True))
    assert href is not None
    assert _message(href) == content.registry.DEFAULTS["whatsapp.message.default"].replace(
        "{brand}", content.registry.DEFAULTS["global.brand"]
    )


# --- the product page names the model ----------------------------------------


def test_a_product_page_pre_writes_its_model_and_url(app: "Flask", client: "FlaskClient") -> None:
    with app.app_context():
        ProductRepository.create(title="Tote Cognac", price=45000, category="Tote")
        pid = ProductRepository.get_published()[0].id

    href = _fab_href(client.get(f"/producto/{pid}").get_data(as_text=True))
    assert href is not None
    message = _message(href)
    assert "Tote Cognac" in message
    assert f"{app.config['SITE_URL']}/producto/{pid}" in unquote(message)


# --- the number is editable copy ---------------------------------------------


def test_a_rewritten_number_reaches_the_link_as_digits(app: "Flask", client: "FlaskClient") -> None:
    _publish(app, "global.whatsapp_number", "+54 (9) 11 2222-3333")
    href = _fab_href(client.get("/").get_data(as_text=True))
    assert href is not None
    assert href.startswith("https://wa.me/5491122223333?text=")


def test_emptying_the_number_hides_the_button(app: "Flask", client: "FlaskClient") -> None:
    _publish(app, "global.whatsapp_number", "")
    html = client.get("/").get_data(as_text=True)
    assert _fab_href(html) is None
    assert "wa.me" not in html


def test_link_helper_normalizes_and_degrades(app: "Flask") -> None:
    with app.test_request_context("/"):
        assert content.whatsapp_link() == "https://wa.me/5491136053910"
        assert content.whatsapp_link("hola  che") == "https://wa.me/5491136053910?text=hola%20che"
        assert content.whatsapp_link("   ") == "https://wa.me/5491136053910"

    _publish(app, "global.whatsapp_number", "11-2222")  # too short to be a real number
    with app.test_request_context("/"):
        assert content.whatsapp_link("hola") == ""


# --- the editable copy stays editable ----------------------------------------


def test_the_message_never_carries_the_editors_key_markers(auth_client: "FlaskClient") -> None:
    """The href goes through t_plain, not t: in the visual editor every resolved string
    is wrapped in private-use marker characters that carry its key. Those are stripped
    from the HTML, but a message URL-encoded into an href is not HTML — they would ride
    into WhatsApp as literal garbage in front of the admin's own message."""
    message = _message(_fab_href(auth_client.get("/?edit=1").get_data(as_text=True)) or "")
    assert message, "the editor canvas renders no WhatsApp link"
    markers = [ch for ch in message if "\ue000" <= ch <= "\uf8ff"]
    assert markers == [], f"editor markers leaked into the chat message: {markers!r}"
    assert "whatsapp.message" not in message
