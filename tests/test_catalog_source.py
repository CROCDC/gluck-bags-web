"""Tests for the storefront catalogue swap (app.services.catalog).

Proves the CATALOG_SOURCE=tiendanube path end to end: the facade returns adapted
mirror products, every storefront page renders them unchanged (home, product detail,
category, sitemap), and — the point of the swap — a cart built from the TN storefront
carries TN ids so the checkout handoff resolves them to variants and returns a
redirect. Default (admin) behaviour is covered by the existing suites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.factory import db
from app.models import TiendaNubeProduct
from app.services import catalog

if TYPE_CHECKING:
    from flask import Flask


def _payload(tn_id: int, name: str, price: int, *, category: str = "Tote",
             published: bool = True, stock: int | None = 5, variant_id: int | None = None,
             description: str | None = None, image_count: int = 1) -> dict[str, Any]:
    return {
        "id": tn_id,
        "name": {"es": name},
        "handle": {"es": name.lower().replace(" ", "-")},
        "description": {"es": description if description is not None else f"Bolso {name} de cuero vegano."},
        "published": published,
        "canonical_url": f"https://tienda.example/{tn_id}",
        "categories": [{"id": 1, "name": {"es": category}}],
        "variants": [{"id": variant_id or tn_id * 10, "price": str(price), "stock": stock, "currency": "ARS"}],
        "images": [
            {"id": tn_id * 100 + n, "src": f"https://cdn.example/{tn_id}-{n}.jpg", "position": n + 1,
             "width": 1080, "height": 1350}
            for n in range(image_count)
        ],
    }


def _seed(app: "Flask", *payloads: dict[str, Any]) -> None:
    with app.app_context():
        for payload in payloads:
            db.session.add(TiendaNubeProduct(tn_id=int(payload["id"])).apply_payload(payload))
        db.session.commit()


def _tn(app: "Flask") -> "Flask":
    """Flip the app to the Tienda Nube storefront source."""
    app.config["CATALOG_SOURCE"] = "tiendanube"
    return app


# --- source selection --------------------------------------------------------


def test_source_defaults_to_admin(app: "Flask") -> None:
    with app.app_context():
        assert catalog.source() == "admin"
        assert catalog.is_tiendanube() is False


def test_source_reads_config(app: "Flask") -> None:
    _tn(app)
    with app.app_context():
        assert catalog.is_tiendanube() is True


# --- facade over the mirror --------------------------------------------------


def test_get_published_returns_only_published_mirror(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote Cognac", 45000), _payload(2, "Mini Rosa", 30000, category="Mini Bag"),
          _payload(3, "Oculto", 10000, published=False))
    _tn(app)
    with app.app_context():
        published = catalog.get_published()
        assert {p.id for p in published} == {1, 2}
        assert all(isinstance(p, catalog.StorefrontProduct) for p in published)


def test_adapter_exposes_product_interface(app: "Flask") -> None:
    _seed(app, _payload(5, "Tote Cognac", 45000))
    _tn(app)
    with app.app_context():
        p = catalog.get_by_id(5)
        assert p.id == 5
        assert p.title == "Tote Cognac"
        assert p.category == "Tote"
        assert p.price == 45000  # int pesos, from the "45000" variant string
        assert p.formatted_price == "$ 45.000"
        assert p.currency == "ARS"
        assert p.is_published is True
        assert p.in_stock is True
        assert p.cover.src == "https://cdn.example/5-0.jpg"
        assert p.cover.width == 1080 and p.cover.height == 1350
        assert len(p.images) == 1 and p.videos == []
        assert catalog.is_purchasable(p) is True


def test_adapter_out_of_stock_is_not_purchasable(app: "Flask") -> None:
    _seed(app, _payload(6, "Agotado", 45000, stock=0))
    _tn(app)
    with app.app_context():
        p = catalog.get_by_id(6)
        assert p.in_stock is False
        assert catalog.is_purchasable(p) is False


def test_published_categories_from_mirror(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote", 45000, category="Tote"), _payload(2, "Mini", 30000, category="Mini Bag"))
    _tn(app)
    with app.app_context():
        assert catalog.published_categories() == ["Tote", "Mini Bag"]


# --- storefront pages render the mirror --------------------------------------


def test_home_renders_mirror_products(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote Cognac", 45000))
    _tn(app)
    html = app.test_client().get("/").get_data(as_text=True)
    assert "Tote Cognac" in html
    assert "$ 45.000" in html
    assert "https://cdn.example/1-0.jpg" in html


def test_product_detail_renders_mirror_product(app: "Flask") -> None:
    _seed(app, _payload(7, "Bucket Negro", 52000, category="Bucket Bag"))
    _tn(app)
    resp = app.test_client().get("/producto/7")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Bucket Negro" in html
    # The add-to-cart button carries the TN product id — the loop's linchpin.
    assert 'data-add-to-cart="7"' in html


def test_unpublished_mirror_product_is_404(app: "Flask") -> None:
    _seed(app, _payload(8, "Oculto", 10000, published=False))
    _tn(app)
    assert app.test_client().get("/producto/8").status_code == 404


def test_category_page_renders_mirror(app: "Flask") -> None:
    _seed(app, _payload(1, "Tote Cognac", 45000, category="Tote"))
    _tn(app)
    resp = app.test_client().get("/categoria/tote")
    assert resp.status_code == 200
    assert "Tote Cognac" in resp.get_data(as_text=True)


def test_sitemap_lists_mirror_products(app: "Flask") -> None:
    _seed(app, _payload(11, "Tote", 45000), _payload(12, "Mini", 30000, category="Mini Bag"))
    _tn(app)
    xml = app.test_client().get("/sitemap.xml").get_data(as_text=True)
    assert "/producto/11" in xml
    assert "/producto/12" in xml


# --- the closed loop: TN storefront -> cart -> checkout ----------------------


def test_cart_add_and_checkout_loop(app: "Flask", monkeypatch) -> None:
    """Add a TN product to the cart and run the checkout handoff: the cart line's id
    is the TN product id, so the resolver maps it to variant 950 with no extra link."""
    _seed(app, _payload(95, "Tote Cognac", 45000, variant_id=950))
    _tn(app)
    client = app.test_client()

    add = client.post("/api/cart/add", json={"product_id": 95, "qty": 1})
    assert add.status_code == 200
    assert add.get_json()["count"] == 1

    captured: dict[str, Any] = {}

    class _TNClient:
        def create_checkout(self, line_items, *, contact=None):
            captured["line_items"] = line_items
            captured["contact"] = contact
            return {"id": 3, "checkout_url": "https://checkout.tiendanube/3"}

    from app.services import checkout_service

    monkeypatch.setattr(checkout_service, "build_client_from_env", lambda: _TNClient())

    data = client.post("/checkout", json={"email": "ana@example.com"}).get_json()
    assert data["ready"] is True
    assert data["redirect_url"] == "https://checkout.tiendanube/3"
    # The resolver turned the TN product id (95) into its first variant (950), and
    # the buyer email became the draft-order contact (TN prefills checkout with it).
    assert captured["line_items"] == [{"variant_id": 950, "quantity": 1}]
    assert captured["contact"] == {"contact_email": "ana@example.com"}


def test_cart_rejects_unpublished_mirror_product(app: "Flask") -> None:
    _seed(app, _payload(20, "Oculto", 45000, published=False))
    _tn(app)
    resp = app.test_client().post("/api/cart/add", json={"product_id": 20})
    assert resp.status_code == 409


# --- SEO under the TN source ---------------------------------------------------
# Prod runs CATALOG_SOURCE=tiendanube, so the user-visible SEO promises must hold
# for mirror-served products too (they regressed silently before: double-scheme
# og:image, hardcoded InStock).


def test_tn_pdp_jsonld_offer_from_mirror_price(app: "Flask") -> None:
    _seed(app, _payload(95, "Tote Cognac", 45000))
    _tn(app)
    html = app.test_client().get("/producto/95").get_data(as_text=True)
    assert '"offers"' in html
    assert '"price": "45000"' in html
    assert '"availability": "https://schema.org/InStock"' in html


def test_tn_pdp_jsonld_out_of_stock(app: "Flask") -> None:
    """The mirror tracks real stock and the cart 409s sold-out products — the
    structured data must say OutOfStock instead of promising availability."""
    _seed(app, _payload(96, "Tote Agotado", 45000, stock=0))
    _tn(app)
    html = app.test_client().get("/producto/96").get_data(as_text=True)
    assert '"availability": "https://schema.org/OutOfStock"' in html


def test_tn_pdp_social_image_is_cdn_url_not_double_prefixed(app: "Flask") -> None:
    """Mirror covers are ABSOLUTE CDN URLs; og:image/twitter:image/JSON-LD image
    must emit them verbatim, never site_url + absolute-URL (invalid double scheme)."""
    _seed(app, _payload(97, "Tote Social", 45000))
    _tn(app)
    html = app.test_client().get("/producto/97").get_data(as_text=True)
    assert 'content="https://cdn.example/97-0.jpg"' in html
    assert "https://gluckbags.comhttps://" not in html
    assert '"image": ["https://cdn.example/97-0.jpg"]' in html


def test_tn_pdp_description_sells_online_not_instagram(app: "Flask") -> None:
    """A priced, purchasable PDP's SERP snippet must sell the online flow; the
    Instagram-availability tail only belongs to the unpriced fallback."""
    _seed(app, _payload(98, "Tote Meta", 45000))
    _tn(app)
    html = app.test_client().get("/producto/98").get_data(as_text=True)
    assert "Comprá online" in html
    assert "Consultá disponibilidad por Instagram" not in html


# --- legacy URL continuity -------------------------------------------------------


def test_legacy_product_ids_301_to_category_under_tn(app: "Flask") -> None:
    """Pre-migration /producto/<id> URLs are indexed by Google; under the TN source
    they must 301 to the legacy product's category, resolved from the still-present
    legacy table (the DB is the truth for what each id was — not seed order)."""
    from app.repositories import ProductRepository

    with app.app_context():
        tote = ProductRepository.create(title="Tote Cognac", category="Tote")
        mini = ProductRepository.create(title="Crossbody Rosa", category="Mini Bag")
        tote_id, mini_id = tote.id, mini.id
    _tn(app)
    client = app.test_client()
    resp = client.get(f"/producto/{tote_id}")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/categoria/tote")
    resp = client.get(f"/producto/{mini_id}")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/categoria/mini-bag")


def test_unknown_tn_product_still_404s(app: "Flask") -> None:
    """An id with no legacy row gets a plain 404 (no redirect invented)."""
    _tn(app)
    assert app.test_client().get("/producto/999999999").status_code == 404


def test_env_boot_with_tiendanube_source_serves_empty_mirror(tmp_path, monkeypatch) -> None:
    """create_app booted with CATALOG_SOURCE=tiendanube from the ENVIRONMENT (the
    prod shape, including the docker-compose fallback) must come up serving the
    empty mirror gracefully: 200s everywhere, no legacy products leaking."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "1")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("CATALOG_SOURCE", "tiendanube")
    from app.factory import create_app

    application = create_app()
    application.testing = True
    client = application.test_client()
    home = client.get("/")
    html = home.get_data(as_text=True)
    assert home.status_code == 200
    assert "Muy pronto" in html
    assert "Tote Cognac" not in html
    assert client.get("/sitemap.xml").status_code == 200


# --- rich-text descriptions from the TN admin ------------------------------------


def test_html_to_text_flattens_tn_rich_text() -> None:
    assert catalog.html_to_text("<p>Cartera tipo tote.</p><p>Cuero vegano.</p>") == (
        "Cartera tipo tote.\n\nCuero vegano."
    )
    assert catalog.html_to_text("L&iacute;neas puras<br>sin costuras") == "Líneas puras\nsin costuras"
    assert catalog.html_to_text("<ul><li>Uno</li><li>Dos</li></ul>") == "Uno\n\nDos"
    assert catalog.html_to_text("texto plano") == "texto plano"
    assert catalog.html_to_text("") == ""
    assert catalog.html_to_text(None) is None
    assert catalog.html_to_text("<p></p>") is None


def test_tn_pdp_renders_description_without_raw_html(app: "Flask") -> None:
    """TN descriptions are rich text; the PDP (and the JSON-LD) must show clean
    paragraphs, never literal escaped tags."""
    _seed(app, _payload(90, "Tote Suela", 45000,
                        description="<p>Cartera tipo tote color suela.</p><p>Hecha a mano.</p>"))
    _tn(app)
    html = app.test_client().get("/producto/90").get_data(as_text=True)
    assert "Cartera tipo tote color suela." in html
    assert "&lt;p&gt;" not in html
    assert "u003cp" not in html


# --- gallery + category chips -----------------------------------------------------


def test_tn_pdp_gallery_thumbs_only_with_multiple_images(app: "Flask") -> None:
    """Multi-image products get a thumbnail strip (one button per slide, outside
    .pdp-gallery so the slide count stays the media count); single-image ones don't."""
    _seed(app, _payload(91, "Tote Multi", 45000, image_count=3),
          _payload(92, "Tote Solo", 45000))
    _tn(app)
    client = app.test_client()

    multi = client.get("/producto/91").get_data(as_text=True)
    assert multi.count('class="pdp-media"') == 3
    assert 'class="pdp-thumbs"' in multi
    assert multi.count("data-gallery-thumb") == 3

    assert "data-gallery-prev" in multi and "data-gallery-next" in multi

    solo = client.get("/producto/92").get_data(as_text=True)
    assert 'class="pdp-thumbs"' not in solo
    assert "data-gallery-prev" not in solo


def test_pdp_category_chips_have_context_label(app: "Flask") -> None:
    _seed(app, _payload(93, "Tote Label", 45000))
    _tn(app)
    html = app.test_client().get("/producto/93").get_data(as_text=True)
    assert "Explorá por categoría" in html
    assert 'class="cat-nav"' in html
