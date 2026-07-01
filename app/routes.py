from typing import Any

from flask import Flask, Response, abort, render_template, url_for

from app.repositories import ProductRepository
from app.seo import (
    breadcrumb_jsonld,
    category_breadcrumb_jsonld,
    dump_jsonld,
    organization_jsonld,
    product_jsonld,
    website_jsonld,
)
from app.utils import slugify

# --- Static site content (the non-product sections of the landing page) -------

CATEGORIES: list[dict[str, str]] = [
    {
        "name": "Tote",
        "tagline": "Para todos los días",
        "image": "img/productos/tote-cognac-01",
    },
    {
        "name": "Mini Bag",
        "tagline": "Lo esencial, en pequeño",
        "image": "img/productos/crossbody-rosa",
    },
    {
        "name": "Bucket Bag",
        "tagline": "Volumen y carácter",
        "image": "img/video-posters/bucket-bag-reveal",
    },
    {
        "name": "Clutch",
        "tagline": "Lo justo y necesario",
        "image": "img/productos/clutch-rosa-sobre",
    },
]

# Editorial trust pages (E-E-A-T). Each renders templates/pages/<template> and is
# listed in the sitemap. Contact details / legal copy are placeholders to refine.
STATIC_PAGES: dict[str, dict[str, str]] = {
    "nosotras": {
        "template": "nosotras.html",
        "title": "Nosotras",
        "description": "Quiénes somos: GLÜCK, bolsos de cuero vegano hechos a mano en Argentina, con diseño minimalista y atemporal.",
    },
    "contacto": {
        "template": "contacto.html",
        "title": "Contacto",
        "description": "Escribinos por Instagram, WhatsApp o email. Te ayudamos a elegir tu bolso GLÜCK y coordinamos el envío.",
    },
    "envios": {
        "template": "envios.html",
        "title": "Envíos",
        "description": "Hacemos envíos a todo el país. Conocé tiempos, costos y cómo se despacha tu pedido GLÜCK.",
    },
    "cambios-y-devoluciones": {
        "template": "cambios.html",
        "title": "Cambios y devoluciones",
        "description": "Política de cambios y devoluciones de GLÜCK: plazos, condiciones y cómo gestionarlos.",
    },
    "terminos": {
        "template": "terminos.html",
        "title": "Términos y condiciones",
        "description": "Términos y condiciones de uso y de compra del sitio de GLÜCK: cómo comprar, coordinar pagos y envíos por Instagram, y las condiciones generales del servicio.",
    },
    "privacidad": {
        "template": "privacidad.html",
        "title": "Política de privacidad",
        "description": "Cómo GLÜCK trata tus datos personales y qué herramientas de medición usa el sitio.",
    },
}


# Short intro copy per category — gives each (otherwise thin) category page unique,
# indexable text above the product grid. Keyed by category name.
CATEGORY_INTRO: dict[str, str] = {
    "Tote": (
        "El bolso de todos los días: amplio, de líneas rectas y hecho de una sola "
        "pieza de cuero vegano. Pensado para que entre todo sin perder la silueta "
        "minimalista y atemporal que define a GLÜCK."
    ),
    "Mini Bag": (
        "Lo esencial, en formato pequeño. Bandoleras y crossbody de cuero vegano "
        "para llevar lo justo con las manos libres, sin resignar diseño ni "
        "carácter."
    ),
    "Bucket Bag": (
        "Volumen y carácter en una silueta tipo balde. Cuero vegano plegado a mano, "
        "espacioso y con una caída suave que la vuelve inconfundible."
    ),
    "Clutch": (
        "Lo justo y necesario para una salida. Sobres y clutches de cuero vegano, "
        "mínimos y elegantes, hechos a mano y sin crueldad animal."
    ),
}


def _category_name_for_slug(slug: str) -> str | None:
    """Map a URL slug back to a category name. Considers both the curated category
    cards and any category actually present on published products."""
    names = [c["name"] for c in CATEGORIES] + ProductRepository.published_categories()
    for name in names:
        if slugify(name) == slug:
            return name
    return None


def register_routes(app: Flask) -> None:
    # Expose slugify to templates so links can build /categoria/<slug> URLs.
    app.add_template_filter(slugify, "slugify")

    @app.route("/")
    def index() -> str:
        site_url = app.config["SITE_URL"]
        jsonld = dump_jsonld([organization_jsonld(site_url), website_jsonld(site_url)])
        # Categories that actually have published products, so the home grid can
        # avoid linking an empty (noindex) category as if it were shoppable. Keyed by
        # slug (not raw name) so an off-casing product category (admin input is
        # free-text, e.g. "tote") still matches the curated card ("Tote").
        published_cats = {slugify(c) for c in ProductRepository.published_categories()}
        context: dict[str, Any] = {
            "categories": CATEGORIES,
            "published_cats": published_cats,
            "products": ProductRepository.get_published(),
            "jsonld": jsonld,
        }
        return render_template("index.html", **context)

    @app.route("/producto/<int:product_id>")
    def product_detail(product_id: int) -> str:
        product = ProductRepository.get_by_id(product_id)
        if product is None or not product.is_published:
            abort(404)
        site_url = app.config["SITE_URL"]
        jsonld = dump_jsonld(
            [product_jsonld(product, site_url), breadcrumb_jsonld(product, site_url)]
        )
        return render_template(
            "product_detail.html",
            product=product,
            related=ProductRepository.get_related(product),
            nav_categories=ProductRepository.published_categories(),
            jsonld=jsonld,
        )

    @app.route("/categoria/<slug>")
    def category_page(slug: str) -> str:
        name = _category_name_for_slug(slug)
        if name is None:
            abort(404)
        site_url = app.config["SITE_URL"]
        products = ProductRepository.get_published_by_category(name)
        return render_template(
            "category.html",
            category=name,
            products=products,
            intro=CATEGORY_INTRO.get(name),
            # Sibling categories (non-empty) for the inter-category nav chips.
            nav_categories=ProductRepository.published_categories(),
            jsonld=dump_jsonld(category_breadcrumb_jsonld(name, site_url)),
            # An empty (but known) category is a thin page — keep it out of the index.
            noindex=not products,
        )

    # Register the editorial trust pages from STATIC_PAGES (one thin view each).
    def _make_static_view(slug: str, meta: dict[str, str]) -> Any:
        def view() -> str:
            return render_template(
                f"pages/{meta['template']}", page_title=meta["title"], page_description=meta["description"]
            )

        view.__name__ = f"page_{slug.replace('-', '_')}"
        return view

    for _slug, _meta in STATIC_PAGES.items():
        _view = _make_static_view(_slug, _meta)
        app.add_url_rule(f"/{_slug}", endpoint=_view.__name__, view_func=_view)

    @app.route("/media/<path:filename>")
    def media_file(filename: str) -> Response:
        # Uploaded media is immutable (new upload = new path), so cache forever.
        from flask import send_from_directory

        resp = send_from_directory(app.config["MEDIA_ROOT"], filename, conditional=True)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.route("/robots.txt")
    def robots() -> Response:
        # Dynamic so the Sitemap line always points at the canonical origin.
        body = render_template("robots.txt", site_url=app.config["SITE_URL"])
        return Response(body, mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap() -> Response:
        site_url = app.config["SITE_URL"]
        # changefreq/priority are hints (Google largely ignores them, but they're
        # cheap and valid); lastmod is the signal it does use — set where we have it.
        urls: list[dict[str, str | None]] = [
            {"loc": f"{site_url}/", "lastmod": None, "changefreq": "weekly", "priority": "1.0"}
        ]
        for slug in STATIC_PAGES:
            urls.append(
                {"loc": f"{site_url}/{slug}", "lastmod": None, "changefreq": "yearly", "priority": "0.3"}
            )
        for name in ProductRepository.published_categories():
            urls.append(
                {
                    "loc": f"{site_url}/categoria/{slugify(name)}",
                    "lastmod": None,
                    "changefreq": "weekly",
                    "priority": "0.8",
                }
            )
        for product in ProductRepository.get_published():
            lastmod = product.updated_at.date().isoformat() if product.updated_at else None
            urls.append(
                {
                    "loc": f"{site_url}/producto/{product.id}",
                    "lastmod": lastmod,
                    "changefreq": "weekly",
                    "priority": "0.7",
                }
            )
        body = render_template("sitemap.xml", urls=urls)
        return Response(body, mimetype="application/xml")
