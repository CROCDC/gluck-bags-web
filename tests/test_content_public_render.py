"""An edit in the admin actually changes the public site — and only when published.

The registry is worth nothing if a key is declared but the page still renders a
hardcoded string, so these tests go the whole way: write an override, request the
real page, assert the new copy is there and the old one is gone.
"""

from __future__ import annotations

import os
import re

import pytest
from flask.testing import FlaskClient

from app.content import registry
from app.repositories import SiteTextRepository as Repo


@pytest.fixture
def publish(app):
    """Publish overrides straight into the live values (no draft round trip)."""

    def _publish(**values: str) -> None:
        with app.test_request_context("/"):
            for key, value in values.items():
                Repo.set_published(key.replace("__", "."), value)
            Repo.save()

    return _publish


# These two seed a product through the admin catalogue and then expect it on the
# storefront. Under CATALOG_SOURCE=tiendanube the storefront reads the TN mirror
# instead, so the product never appears — a property of the fixture, not of the copy.
needs_admin_catalogue = pytest.mark.skipif(
    os.environ.get("TEST_CATALOG_SOURCE", "admin") != "admin",
    reason="necesita el catálogo admin para tener un producto en la vidriera",
)


def _create_product(auth_client: FlaskClient, **kwargs) -> None:
    data = {"title": "Bolso Test", "category": "Tote", "price": "45000", "is_published": "on"}
    data.update({k: v for k, v in kwargs.items() if v is not None})
    auth_client.post("/admin/products/new", data=data, follow_redirects=True)


# --- the shared chrome ---------------------------------------------------------


def test_the_brand_is_editable_everywhere_at_once(publish, client: FlaskClient) -> None:
    publish(**{"global__brand": "OTRA MARCA"})
    for path in ("/", "/carrito", "/nosotras"):
        html = client.get(path).get_data(as_text=True)
        assert "OTRA MARCA" in html, path
        assert "GLÜCK" not in html, path


def test_the_tagline_reaches_the_hero_the_title_and_the_footer(
    publish, client: FlaskClient
) -> None:
    publish(**{"global__tagline": "Carteras de otro planeta"})
    html = client.get("/").get_data(as_text=True)
    assert html.count("Carteras de otro planeta") >= 3  # <title>, hero H1, footer


def test_the_menu_labels_are_editable(publish, client: FlaskClient) -> None:
    publish(**{"nav__collection": "Nuestra línea", "nav__cta": "Quiero uno"})
    html = client.get("/").get_data(as_text=True)
    assert html.count("Nuestra línea") == 3  # header, mobile menu, footer
    assert "Quiero uno" in html
    assert ">Colección<" not in html


def test_the_footer_copyright_is_editable_and_keeps_its_tokens(
    publish, client: FlaskClient
) -> None:
    from datetime import datetime

    publish(**{"footer__copyright": "Hecho por {brand} en {year}"})
    html = client.get("/").get_data(as_text=True)
    assert f"Hecho por GLÜCK en {datetime.now().year}" in html


def test_the_instagram_link_is_editable(publish, client: FlaskClient) -> None:
    publish(**{"global__instagram_url": "https://instagram.com/otra_cuenta"})
    html = client.get("/").get_data(as_text=True)
    assert "https://instagram.com/otra_cuenta" in html
    assert "instagram.com/gluck_bags" not in html


# --- the home ------------------------------------------------------------------


def test_every_home_section_heading_is_editable(publish, client: FlaskClient) -> None:
    publish(
        **{
            "home__hero__eyebrow": "AAA",
            "home__hero__subtitle": "BBB",
            "home__categories__title": "CCC",
            "home__feature__eyebrow": "DDD",
            "home__shop__title": "EEE",
            "home__materia__caption": "FFF",
            "home__manifesto__eyebrow": "GGG",
            "home__closer__cta": "HHH",
        }
    )
    html = client.get("/").get_data(as_text=True)
    for marker in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"):
        assert marker in html, marker


def test_list_copy_is_editable_item_by_item(publish, client: FlaskClient) -> None:
    publish(**{"home__feature__specs": "Primero\nSegundo"})
    html = client.get("/").get_data(as_text=True)
    assert "<li>Primero</li>" in html and "<li>Segundo</li>" in html
    assert "De una sola pieza, sin costuras" not in html


def test_the_marquee_accepts_any_number_of_phrases(publish, client: FlaskClient) -> None:
    publish(**{"home__marquee__phrases": "Una\nDos"})
    html = client.get("/").get_data(as_text=True)
    # The track repeats the list twice.
    assert html.count(">Una<") == 2 and html.count(">Dos<") == 2


def test_rich_copy_keeps_its_line_break(publish, client: FlaskClient) -> None:
    publish(**{"home__feature__title": "Una línea<br>y otra"})
    assert "Una línea<br>y otra" in client.get("/").get_data(as_text=True)


def test_the_image_alt_text_is_editable(publish, client: FlaskClient) -> None:
    publish(**{"home__hero__image_alt": "Una foto de un bolso"})
    assert 'alt="Una foto de un bolso"' in client.get("/").get_data(as_text=True)


# --- SEO / social --------------------------------------------------------------


def test_the_home_title_and_description_are_editable(publish, client: FlaskClient) -> None:
    publish(
        **{
            "seo__home__title": "Título propio",
            "seo__home__description": "Descripción propia del sitio.",
        }
    )
    html = client.get("/").get_data(as_text=True)
    assert "<title>Título propio</title>" in html
    assert 'name="description" content="Descripción propia del sitio."' in html
    # og/twitter reuse the page title+description, so the card follows along.
    assert 'property="og:title"            content="Título propio"' in html


def test_the_organization_markup_follows_the_edited_brand(publish, client: FlaskClient) -> None:
    publish(**{"global__brand": "MARCA JSONLD"})
    html = client.get("/").get_data(as_text=True)
    jsonld = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert jsonld and "MARCA JSONLD" in jsonld.group(1)


def test_the_editorial_page_title_description_and_body_are_editable(
    publish, client: FlaskClient
) -> None:
    publish(
        **{
            "page__about__title": "Quiénes somos",
            "page__about__meta_description": "Una descripción nueva.",
            "page__about__body": "<p>Un texto totalmente nuevo</p><h2>Con subtítulo</h2>",
        }
    )
    html = client.get("/nosotras").get_data(as_text=True)
    assert "<title>Quiénes somos · GLÜCK</title>" in html
    assert 'content="Una descripción nueva."' in html
    assert "<p>Un texto totalmente nuevo</p>" in html
    assert "<h2>Con subtítulo</h2>" in html
    assert "nació de una idea simple" not in html


# --- category / product --------------------------------------------------------


def test_the_category_label_changes_the_page_without_moving_its_url(
    publish, client: FlaskClient
) -> None:
    """The slug is identity (rankings, product rows); only the label is copy."""
    publish(**{"category__tote__label": "Totes XL"})
    response = client.get("/categoria/tote")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Totes XL" in html
    assert '<h1 class="section-title">Tote</h1>' not in html
    # …and the URL, the canonical and the breadcrumb markup still say /categoria/tote.
    assert 'rel="canonical" href="https://gluckbags.com/categoria/tote"' in html
    assert "https://gluckbags.com/categoria/tote" in html


def test_the_category_intro_is_editable(publish, client: FlaskClient) -> None:
    publish(**{"category__tote__intro": "Una intro nueva para los totes."})
    html = client.get("/categoria/tote").get_data(as_text=True)
    assert "Una intro nueva para los totes." in html


def test_the_home_category_card_uses_the_edited_label(publish, client: FlaskClient) -> None:
    publish(**{"category__clutch__label": "Sobres", "category__clutch__tagline": "Mínimos"})
    html = client.get("/").get_data(as_text=True)
    assert '<h3 class="cat-name">Sobres</h3>' in html
    assert "Mínimos" in html


@needs_admin_catalogue
def test_the_product_page_labels_and_meta_are_editable(
    publish, auth_client: FlaskClient, client: FlaskClient
) -> None:
    _create_product(auth_client)
    publish(
        **{
            "product__cta_add": "Lo quiero",
            "product__related__title": "Te puede gustar",
            "product__meta__title": "{title} de {brand}",
        }
    )
    product_id = re.search(r"/producto/(\d+)", client.get("/").get_data(as_text=True))
    assert product_id, "the seeded product should be linked from the home"
    html = client.get(f"/producto/{product_id.group(1)}").get_data(as_text=True)
    assert "Lo quiero" in html
    assert "Agregar al carrito" not in html
    assert "<title>Bolso Test de GLÜCK</title>" in html


@needs_admin_catalogue
def test_the_price_fallback_is_editable(
    publish, auth_client: FlaskClient, client: FlaskClient
) -> None:
    _create_product(auth_client, price="")
    publish(**{"product__price_fallback": "Preguntanos"})
    html = client.get("/").get_data(as_text=True)
    assert "Preguntanos" in html and ">Consultar<" not in html


# --- cart / thanks / 404 -------------------------------------------------------


def test_the_cart_copy_is_editable(publish, client: FlaskClient) -> None:
    publish(
        **{
            "cart__page__title": "Tu bolsa",
            "cart__page__empty": "No hay nada acá.",
            "cart__checkout__button": "Pagar ahora",
        }
    )
    html = client.get("/carrito").get_data(as_text=True)
    assert "Tu bolsa" in html and "No hay nada acá." in html and "Pagar ahora" in html


def test_the_cart_drawer_copy_reaches_the_browser_as_data(publish, client: FlaskClient) -> None:
    """The drawer is rendered by cart.js, so its strings ship as inline JSON."""
    publish(**{"cart__page__empty": "Carrito vacío, che."})
    html = client.get("/").get_data(as_text=True)
    payload = re.search(r'id="cartStrings">(.*?)</script>', html, re.S)
    assert payload is not None
    import json

    assert json.loads(payload.group(1))["empty"] == "Carrito vacío, che."


def test_the_404_copy_is_editable(publish, client: FlaskClient) -> None:
    publish(**{"error404__title": "Nada por acá", "error404__cta_primary": "Al inicio"})
    response = client.get("/una-url-que-no-existe")
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert "Nada por acá" in html and "Al inicio" in html


def test_the_thanks_page_copy_is_editable(publish, client: FlaskClient) -> None:
    publish(**{"thanks__title": "¡Listo!", "thanks__cta_primary": "Ver más bolsos"})
    html = client.get("/gracias").get_data(as_text=True)
    assert "¡Listo!" in html and "Ver más bolsos" in html


# --- drafts stay private -------------------------------------------------------


def test_a_draft_is_invisible_to_the_public(app, auth_client: FlaskClient, client: FlaskClient) -> None:
    auth_client.post(
        "/admin/content/home", data={"home.hero.subtitle": "SOLO BORRADOR", "action": "save"}
    )
    assert "SOLO BORRADOR" not in client.get("/").get_data(as_text=True)
    assert "SOLO BORRADOR" not in client.get("/?preview=1").get_data(as_text=True)


def test_the_admin_sees_the_draft_in_preview_mode(auth_client: FlaskClient) -> None:
    auth_client.post(
        "/admin/content/home", data={"home.hero.subtitle": "SOLO BORRADOR", "action": "save"}
    )
    assert "SOLO BORRADOR" not in auth_client.get("/").get_data(as_text=True)
    assert "SOLO BORRADOR" in auth_client.get("/?preview=1").get_data(as_text=True)


def test_a_preview_response_is_never_indexable_or_cacheable(auth_client: FlaskClient) -> None:
    response = auth_client.get("/?preview=1")
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" in response.headers["Cache-Control"]


def test_a_normal_response_keeps_its_headers(auth_client: FlaskClient) -> None:
    response = auth_client.get("/")
    assert "X-Robots-Tag" not in response.headers


# --- nothing changed by default ------------------------------------------------


def test_with_an_empty_database_the_site_renders_its_original_copy(
    client: FlaskClient,
) -> None:
    html = client.get("/").get_data(as_text=True)
    for key in (
        "home.hero.eyebrow",
        "home.categories.title",
        "home.materia.caption",
        "footer.badges",
    ):
        assert registry.FIELDS[key].default in html, key
