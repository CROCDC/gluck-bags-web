"""The catalogue of editable strings for the GLÜCK site (flask-sitecopy 0.5.0).

Every user-facing string the public templates render through ``t('…')`` is declared
here once, with a human label and the current copy as its default. The registry is the
source of truth: a fresh database renders exactly these defaults (no seeding), and the
admin panel at ``/admin/content`` edits the overrides on top.

Product names, prices and category labels are NOT here — those come from the product
catalogue (the admin CRUD / Tienda Nube), and the editor is told so via
``external_content`` in the extension wiring, so a click on them explains where to edit.

Field types used: ``line`` (short), ``text`` (paragraph), ``lines`` (one item per line),
``rich`` (allow-listed HTML — used for headings with <br> and the manifesto <em>),
``url``, ``image`` and ``video`` (which store a location, editable/uploadable from the
panel). ``{brand}``, ``{tagline}`` and ``{instagram_url}`` are site-wide tokens.
"""

from __future__ import annotations

from sitecopy import Group, Registry, Section, TextField

# --- Global: brand tokens + shared assets -------------------------------------------

GLOBAL = Group(
    key="global",
    title="Marca",
    description="Nombre, lema y datos que se repiten en todo el sitio.",
    preview_path="/",
    sections=(
        Section(
            key="brand",
            title="Identidad",
            note="Se usan en todo el sitio. {brand}, {tagline} e {instagram_url} pueden "
            "insertarse en cualquier otro texto.",
            fields=(
                TextField("global.brand", "Nombre de la marca", "GLÜCK", max_length=40),
                TextField(
                    "global.tagline",
                    "Lema",
                    "Bolsos minimalistas de cuero vegano",
                    max_length=120,
                ),
                TextField(
                    "global.instagram_url",
                    "Link de Instagram",
                    "https://www.instagram.com/gluck_bags/",
                    type="url",
                ),
                TextField(
                    "global.og_image",
                    "Imagen para redes (compartir el link)",
                    "/static/img/og-image.jpg",
                    type="image",
                    hint="1200×630. Es la imagen que aparece al compartir el sitio en "
                    "WhatsApp, Instagram o Google.",
                ),
            ),
        ),
    ),
)

# --- Inicio -------------------------------------------------------------------------

HOME = Group(
    key="home",
    title="Inicio",
    description="La portada del sitio.",
    preview_path="/",
    sections=(
        Section(
            key="hero",
            title="Portada",
            note="Lo primero que se ve al entrar.",
            fields=(
                TextField("home.hero.eyebrow", "Antetítulo", "Cuero vegano · Sin costuras"),
                TextField("home.hero.title", "Título", "{tagline}", max_length=120),
                TextField(
                    "home.hero.subtitle",
                    "Bajada",
                    "Bolsos de líneas puras, pensados para durar. Sin pieles, sin excesos.",
                    type="text",
                    max_length=200,
                ),
                TextField("home.hero.cta_primary", "Botón principal", "Ver la colección"),
                TextField("home.hero.cta_secondary", "Botón secundario", "Comprar ahora"),
            ),
        ),
        Section(
            key="marquee",
            title="Cinta que se desliza",
            note="Las frases que corren en la tira animada, una por línea.",
            fields=(
                TextField(
                    "home.marquee.phrases",
                    "Frases",
                    "Cuero vegano minimalista\nDe una sola pieza\nDiseño atemporal\nSin crueldad",
                    type="lines",
                ),
            ),
        ),
        Section(
            key="categorias",
            title="La colección",
            note="El encabezado de la grilla de categorías. Los nombres de cada categoría "
            "se editan en Productos.",
            fields=(
                TextField("home.categorias.eyebrow", "Antetítulo", "La colección"),
                TextField(
                    "home.categorias.title",
                    "Título",
                    "Cuatro siluetas, infinitas combinaciones",
                    max_length=120,
                ),
            ),
        ),
        Section(
            key="feature",
            title="Diseño de autor",
            note="La franja de imagen + texto.",
            fields=(
                TextField("home.feature.eyebrow", "Antetítulo", "Diseño de autor"),
                TextField(
                    "home.feature.title",
                    "Título",
                    "El Tote que te sigue<br>a todas partes",
                    type="rich",
                    max_length=120,
                ),
                TextField(
                    "home.feature.body",
                    "Texto",
                    "Cada bolso sale de una sola pieza de cuero vegano: la diseñamos, la "
                    "cortamos con láser y la plegamos a mano. Sin costuras — la forma se "
                    "sostiene con remaches y broches.",
                    type="text",
                    max_length=400,
                ),
                TextField(
                    "home.feature.specs",
                    "Características",
                    "De una sola pieza, sin costuras\nCortada con láser desde un molde propio\n"
                    "Plegada y unida con remaches y broches",
                    type="lines",
                ),
                TextField("home.feature.cta", "Botón", "Quiero el mío"),
            ),
        ),
        Section(
            key="shop",
            title="Bolsos",
            note="El encabezado de la grilla de productos.",
            fields=(
                TextField("home.shop.eyebrow", "Antetítulo", "Bolsos"),
                TextField("home.shop.title", "Título", "Lo último de {brand}", max_length=120),
            ),
        ),
        Section(
            key="materia",
            title="La materia (video)",
            note="La sección del video del cuero.",
            fields=(
                TextField("home.materia.eyebrow", "Antetítulo", "La materia"),
                TextField(
                    "home.materia.title",
                    "Título",
                    "Cuero vegano,<br>elegido a mano",
                    type="rich",
                    max_length=120,
                ),
                TextField(
                    "home.materia.body",
                    "Texto",
                    "Seleccionamos cada rollo por su tacto y su color. Materiales libres de "
                    "origen animal que envejecen con gracia, para que cada bolso cuente su "
                    "propia historia.",
                    type="text",
                    max_length=400,
                ),
                TextField(
                    "home.materia.caption",
                    "Epígrafe",
                    "Detrás de escena · selección de materiales",
                ),
                TextField(
                    "home.materia.poster",
                    "Poster del video",
                    "/static/img/video-posters/cuero-materia-prima.jpg",
                    type="image",
                    hint="La imagen fija que se ve antes de que cargue el video.",
                ),
            ),
        ),
        Section(
            key="manifiesto",
            title="Nosotras",
            note="El manifiesto y los tres valores.",
            fields=(
                TextField("home.manifiesto.eyebrow", "Antetítulo", "Nosotras"),
                TextField(
                    "home.manifiesto.quote",
                    "Frase principal",
                    "Creemos en lo simple, en lo que dura y en lo que no le hace daño a nadie. "
                    "Cada <em>{brand}</em> nace de esa idea: belleza sin culpa.",
                    type="rich",
                    max_length=400,
                ),
                TextField("home.manifiesto.v1_title", "Valor 1 · título", "100% vegano"),
                TextField(
                    "home.manifiesto.v1_body", "Valor 1 · texto",
                    "Sin pieles ni derivados animales.", type="text", max_length=160,
                ),
                TextField("home.manifiesto.v2_title", "Valor 2 · título", "Sin costuras"),
                TextField(
                    "home.manifiesto.v2_body", "Valor 2 · texto",
                    "De una sola pieza, plegada y unida con remaches.", type="text", max_length=160,
                ),
                TextField("home.manifiesto.v3_title", "Valor 3 · título", "Atemporal"),
                TextField(
                    "home.manifiesto.v3_body", "Valor 3 · texto",
                    "Diseños que no pasan de moda.", type="text", max_length=160,
                ),
            ),
        ),
        Section(
            key="closer",
            title="Cierre / regalo",
            note="La sección final con el video de packaging.",
            fields=(
                TextField("home.closer.eyebrow", "Antetítulo", "Listo para regalar"),
                TextField(
                    "home.closer.title",
                    "Título",
                    "Cada pedido llega<br>como un regalo",
                    type="rich",
                    max_length=120,
                ),
                TextField(
                    "home.closer.body",
                    "Texto",
                    "Empaque kraft con el sello {brand}. Hacé tu pedido por Instagram y te "
                    "acompañamos en todo el proceso.",
                    type="text",
                    max_length=300,
                ),
                TextField(
                    "home.closer.specs",
                    "Características",
                    "Envíos a todo el país\nPackaging de regalo incluido\n"
                    "Atención personalizada por mensaje",
                    type="lines",
                ),
                TextField("home.closer.cta", "Botón", "Escribinos por Instagram"),
                TextField(
                    "home.closer.video",
                    "Video de cierre",
                    "/static/video/packaging-desktop.mp4",
                    type="video",
                    hint="Subí o pegá el link de un video (mp4/webm).",
                ),
            ),
        ),
        Section(
            key="meta",
            title="Buscadores y redes",
            note="Lo que ve Google y lo que aparece al compartir el link. No se ve en la página.",
            fields=(
                TextField(
                    "home.meta.title", "Título en Google", "{brand} · {tagline}", max_length=140
                ),
                TextField(
                    "home.meta.description",
                    "Descripción en Google",
                    "{brand} — {tagline}. Carteras y bolsos de cuero vegano, hechos a mano con "
                    "un diseño minimalista y atemporal.",
                    type="text",
                    max_length=300,
                ),
            ),
        ),
    ),
)

# --- Pie de página ------------------------------------------------------------------

FOOTER = Group(
    key="footer",
    title="Pie de página",
    description="El bloque final, en todas las páginas.",
    preview_path="/",
    sections=(
        Section(
            key="cta",
            title="Llamado final",
            fields=(
                TextField("footer.cta_eyebrow", "Antetítulo", "¿Te gustó algo?"),
                TextField("footer.cta_link", "Texto del link", "Seguinos en Instagram"),
                TextField("footer.bottom_note", "Nota final", "Cuero vegano · Hecho a mano"),
            ),
        ),
    ),
)


REGISTRY = Registry(
    groups=(GLOBAL, HOME, FOOTER),
    tokens=("global.brand", "global.tagline", "global.instagram_url"),
)
