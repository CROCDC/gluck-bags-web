#!/usr/bin/env python3
"""Consolidate auto + workflow + authored SEO findings into a single curated findings.json.
Dedupes overlapping findings, drops the deterministic false-positive, folds the 6 scattered
internal-search findings into one, and adds a human `symptom` line to each card."""
import json, pathlib

WORK = pathlib.Path(__file__).resolve().parent
ART = WORK / "artifacts"

wf = json.loads((WORK / "workflow-result-inner.json").read_text())["confirmed"]
auto = json.loads((ART / "seo-findings-auto.json").read_text())["findings"]

# --- workflow findings to DROP (duplicates / folded elsewhere), matched by title substring ---
DROP = [
    "sin contenido descriptivo único y Product schema sin offers",   # dup of offers finding
    "Páginas de categoría sin texto introductorio",                  # dup of thin-content table
    "Categoría vacía con noindex (/categoria/bucket-bag) enlazada",   # dup of bucket-bag medium
    "http:// no redirige a https:// — la versión HTTP sirve 200",     # dup of the other http finding
    "La página de resultados de búsqueda hereda la tarjeta OG",       # folded into search finding
    "HSTS sin preload",                                               # folded into http finding
    "Espacio ilimitado de URLs de búsqueda interna",                 # folded into search finding
]

# --- human symptom per kept workflow finding (title substring -> symptom) ---
SYMPTOM = {
    "Product schema sin offers": "Los productos exponen JSON-LD Product pero sin el bloque offers (ni review/aggregateRating), que Google exige para cualquier rich result de producto. Search Console lo marca como inválido y la ficha no gana snippet enriquecido. Matiz: la PDP no publica precio (dice 'Consultar', compra por Instagram), así que un offers con precio real no es posible hoy.",
    "Product schema sin identificadores": "El Product no trae sku/gtin/mpn ni aggregateRating/review, y su brand es un Brand suelto sin @id que lo una a la Organization de la home. Se pierde consolidación de entidad de marca y señales recomendadas.",
    "Páginas de categoría sin ningún structured data": "Las categorías no emiten ningún JSON-LD: no hay BreadcrumbList (aunque la jerarquía existe) ni ItemList/CollectionPage, desaprovechando structured data en páginas de colección.",
    "BreadcrumbList de las PDP no coincide": "El BreadcrumbList JSON-LD del producto no refleja exactamente el rastro de navegación visible, lo que puede generar inconsistencia markup-vs-contenido.",
    "Organization de la home demasiado genérica": "El Organization de la home es mínimo: sin tipo más específico, sin contactPoint/address y con sameAs pobre, desaprovechando el panel de conocimiento de marca.",
    "Contenido placeholder \"(a completar)\"": "Tres páginas de confianza (contacto, envíos, cambios y devoluciones) están publicadas e indexables con texto de plantilla '(a completar)' en lugar de los datos reales (WhatsApp, email, plazos). Es una señal E-E-A-T negativa visible para usuarios y Google.",
    "Ausencia total de señales E-E-A-T": "No hay autor/fundadora nombrada, ni NAP (dirección/teléfono), ni contactPoint en el schema, ni fechas de publicación/actualización en ninguna página. Para una marca artesanal, faltan las señales de experiencia y confianza que Google valora.",
    "La home enlaza /categoria/bucket-bag": "La home promociona 'Bucket Bag' como una de sus 4 categorías principales, pero esa página es noindex, no tiene productos y está fuera del sitemap. Es un dead-end de crawl y una mala experiencia (el usuario llega a una categoría vacía).",
    "Las fichas de producto no tienen ningún enlace crawleable": "El breadcrumb producto→categoría existe solo como JSON-LD; en el HTML no hay ningún <a> a la categoría padre. No se transmite link equity producto→categoría ni hay navegación real hacia la colección.",
    "Las categorías están débilmente enlazadas": "Cada categoría recibe enlaces solo de la home y la búsqueda, no se enlazan entre sí, y el botón 'volver' apunta a un fragmento (#shop) en vez de a una URL de catálogo indexable. La arquitectura interna es plana y débil.",
    "El anchor text de las fichas de producto": "El enlace de cada tarjeta de producto usa como texto de ancla todo el bloque ('Ver más … Consultar'), mezclando CTA y repeticiones en vez de un ancla limpia con el nombre del producto. Diluye la keyword del enlace interno.",
    "No hay breadcrumbs HTML visibles": "Ninguna página tiene breadcrumbs HTML reales (ni markup aria-label=breadcrumb); solo las PDP tienen BreadcrumbList en JSON-LD sin contraparte visible. Falta rastro de navegación para usuario y crawler.",
    "robots.txt NO bloquea el sitio": "Aclaración: el análisis determinístico marcó un 'Disallow: / global' como crítico, pero es un falso positivo. El grupo User-agent:* tiene Allow:/, y los Disallow:/ aplican solo a bots de IA (GPTBot, ClaudeBot, Google-Extended, etc.). Googlebot indexa con normalidad. Sin acción requerida.",
    "El sitemap.xml no está declarado en robots.txt": "El robots.txt no incluye la directiva 'Sitemap:', así que los crawlers no descubren el sitemap por esa vía (sí existe en /sitemap.xml).",
    "HTTP no redirige a HTTPS": "http://gluckbags.com/ responde 200 con la página completa en lugar de un 301 a https://. Queda una copia rastreable por HTTP (duplicado de protocolo) y la primera request viaja en texto plano antes de que actúe el HSTS. El canonical en https mitiga el SEO, pero el 301 duro es trivial y conviene.",
    "URLs con barra final hacen 404": "Agregar una barra final (p. ej. /categoria/tote/) devuelve un 404 duro en vez de redirigir 301 a la versión canónica. Un enlace interno o externo con slash extra rompe en vez de resolver.",
    "Sitemap pobre": "El sitemap declara 15 URLs pero 10 no tienen <lastmod>, y ninguna tiene changefreq/priority. Le da a Google menos señales de frescura/prioridad.",
    "Doble barra en la raíz": "https://gluckbags.com// (doble barra) devuelve 200 con la home completa en vez de 301/404, abriendo una variante de URL duplicada menor.",
    "International SEO: N/A": "Verificación N/A: el sitio es monolingüe (es-AR) y mono-región, por lo que no necesita hreflang ni x-default, y no los declara. Correcto.",
    "is generic Spanish while the store self-targets": "El <html lang=\"es\"> es español genérico mientras el sitio se autodirige a Argentina (og:locale=es_AR). Inconsistencia menor de señal de idioma/región.",
    "og:url apunta a la home en todas las páginas internas": "Todas las páginas internas (categorías, artículos, estáticas, búsqueda, 404) declaran og:url=home, que no coincide con su canonical autorreferente. Al compartirlas, las redes consolidan la interacción social hacia la home y no hacia la URL real. (Las PDP sí tienen og:url correcto.)",
    "Categorías, artículos y páginas estáticas comparten la MISMA tarjeta social": "Categorías, artículos y estáticas emiten la misma tarjeta OG genérica de la home (mismo og:title/description/image). Compartir cualquiera de esas páginas muestra el preview de la home, perdiendo CTR social y contexto.",
    "og:image de producto es vertical/cuadrada": "Las PDP usan la foto del producto como og:image en formato retrato/cuadrado (787×1400, 1080×1080) en vez de 1200×630, y sin declarar og:image:width/height. El preview social se recorta y puede renderizar sin dimensiones reservadas.",
    "La página 404 emite una tarjeta social completa": "La página 404 (correctamente noindex) igual renderiza la tarjeta OG completa de la home. Compartir un enlace roto del sitio muestra un preview legítimo de la home (soft-404 social).",
    "Category listing: la primera imagen de producto": "En los listados de categoría, la primera imagen de producto —candidata a LCP, above-the-fold en mobile— sale con loading=lazy y sin fetchpriority. El sitio ya aplica bien el patrón eager/priority en home y PDP; falta replicarlo en los listados.",
    "LCP del home = 2.7 s": "El LCP del home en Lighthouse mobile es 2.7s (score 0.85), la única métrica CWV fuera de 'good' (FCP, CLS, TBT, SI están todas bien). El LCP es texto (H1 del hero).",
    "Imágenes de producto (/media/) se sirven en WebP/JPG sin variante AVIF": "Las imágenes de producto se sirven en WebP/JPG pero sin AVIF, a diferencia del hero del home que sí tiene AVIF. AVIF ahorraría 30–50% extra en la imagen principal del PDP (que es su LCP).",
    "El bloque <style> inline": "El CSS crítico inline (presente en cada documento) no está minificado: ~2.2 KiB de whitespace/comentarios viajan en cada respuesta HTML y se parsean antes del primer paint.",
    "Sin interstitial intrusivo ni cookie wall": "Confirmación positiva: no hay pop-ups, cookie walls ni interstitials que tapen el contenido al cargar. El sitio no incurre en la penalización mobile de Google por interstitials intrusivos. Sin acción.",
    "La conversión principal": "La única conversión del negocio —el clic saliente del CTA 'Comprar' hacia la tienda de Instagram— no se mide en ninguna herramienta (Umami y Cloudflare solo registran pageviews). No se sabe cuántas visitas terminan en intención de compra.",
    "El tag de Umami no está acotado por dominio": "El tag de Umami no tiene data-domains, así que cuenta pageviews desde cualquier host donde cargue el script (alias gluck.nexttech.com.ar, entornos de dev), contaminando las estadísticas del dominio productivo.",
}

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

out = []

def add(f):
    out.append(f)

# 1) Workflow findings (curated)
for f in wf:
    title = f.get("title", "")
    if any(d in title for d in DROP):
        continue
    sym = next((s for k, s in SYMPTOM.items() if k in title), None)
    g = {
        "title": title,
        "severity": f.get("severity", "low"),
        "category": f.get("category", ""),
        "dimension": f.get("dimension", ""),
        "scope": f.get("scope", ""),
        "symptom": sym,
        "evidence": f.get("evidence", ""),
        "recommendation": f.get("recommendation", ""),
        "evidenceKind": f.get("evidenceKind", "code"),
        "status": "PENDIENTE",
        "source": "workflow",
    }
    add(g)

# 2) Authored consolidated finding: internal search (folds 6 scattered findings)
add({
    "title": "Búsqueda interna indexable y sin límite: duplica la home y abre un espacio infinito de URLs",
    "severity": "medium",
    "category": "Crawl / Indexación",
    "dimension": "indexability",
    "scope": "/?s=<cualquier-término>",
    "symptom": "La página de resultados de búsqueda interna responde 200 sin noindex. Comparte <title> y meta description con la home, su canonical apunta a la home (no autorreferente) y, como /?s= acepta cualquier término, genera infinitas URLs rastreables que reparten crawl budget y se ven como contenido duplicado/thin. Además hereda la tarjeta social de la home.",
    "evidence": "GET https://gluckbags.com/?s=seo → HTTP 200; meta robots: None; X-Robots-Tag: None. <title> y <meta name=\"description\"> idénticos a la home. <link rel=\"canonical\"> → https://gluckbags.com/ (no autorreferente). Cualquier otro término (/?s=loquesea) también responde 200, por lo que el espacio de URLs es ilimitado.",
    "evidenceCode": "GET /?s=seo\nHTTP/2 200\nmeta robots: None\nX-Robots-Tag: None\n<title>GLÜCK · Bolsos minimalistas de cuero vegano</title>   <!-- = home -->\n<link rel=\"canonical\" href=\"https://gluckbags.com/\">          <!-- apunta a home, no a sí misma -->",
    "recommendation": "Agregar <meta name=\"robots\" content=\"noindex,follow\"> a las páginas de búsqueda (?s=). Opcional reforzar con Disallow: /?s= en robots.txt. Quitar (o minimizar) el bloque OG de esas páginas para que no se compartan como la home. Esto elimina el duplicado y cierra el espacio infinito de URLs.",
    "evidenceKind": "code",
    "status": "PENDIENTE",
    "source": "consolidated",
})

# 3) Auto findings with quantified tables (kept; add symptom)
AUTO_KEEP = {
    "9 páginas con contenido delgado": "Nueve páginas tienen menos de 300 palabras: las 3 categorías (55–62 palabras, sin texto introductorio) y las páginas legales/informativas. Las categorías thin rinden poco y compiten débilmente; conviene sumar copy introductorio único por categoría.",
    "3 títulos demasiado cortos": "Tres títulos quedan por debajo de 30 caracteres (Nosotras·GLÜCK, Envíos·GLÜCK, Contacto·GLÜCK): son correctos pero desaprovechan espacio para sumar contexto/keyword.",
    "3 páginas con saltos en la jerarquía": "Las 3 categorías saltan de H1 (nombre de la categoría) a H3 (nombres de producto) sin H2 intermedio. Jerarquía de encabezados inconsistente.",
    "1 meta descriptions fuera de rango": "La meta description de /terminos es muy corta (61 caracteres); por debajo del rango útil de 120–160. Menor.",
}
for f in auto:
    title = f.get("title", "")
    sym = next((s for k, s in AUTO_KEEP.items() if k in title), None)
    if sym is None:
        continue
    g = dict(f)
    g["symptom"] = sym
    g["status"] = "PENDIENTE"
    g["source"] = "auto"
    add(g)

# Sort by severity, then category; assign ids
out.sort(key=lambda f: (SEV_RANK.get(f.get("severity", "low"), 5), f.get("category", "")))
for i, f in enumerate(out, 1):
    f["id"] = f"{i:02d}"

doc = {
    "site": "https://gluckbags.com",
    "date": "2026-06-29",
    "summary": (
        "Auditoría SEO exhaustiva de gluckbags.com (e-commerce/catálogo Flask SSR, marca GLÜCK, "
        "es-AR monolingüe, sin checkout: la compra se coordina por Instagram). Base técnica muy sólida "
        "(Lighthouse SEO 100, Performance 96, CLS 0; titles únicos, canonical autorreferente, OG e imágenes "
        "bien resueltas; el viejo bug de canonical hacia el alias gluck.nexttech.com.ar ya está corregido). "
        "Los hallazgos son de refinamiento: completitud de structured data (Product sin offers), OG por-página "
        "(og:url y tarjeta social heredan la home), arquitectura de enlazado interno (categorías y productos débilmente "
        "enlazados, breadcrumbs solo en JSON-LD), señales E-E-A-T y de confianza (placeholder '(a completar)' en páginas "
        "de contacto/envíos), indexación de la búsqueda interna y medición de la conversión a Instagram. "
        "Sin hallazgos críticos ni altos. Sin datos de Google Search Console (no había credenciales): la indexación/posición "
        "real no se midió contra la API oficial."
    ),
    "findings": out,
}
(WORK / "findings.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))

# quick stats
from collections import Counter
c = Counter(f["severity"] for f in out)
print(f"TOTAL findings: {len(out)}")
print("by severity:", dict(c))
print("by source:", dict(Counter(f["source"] for f in out)))
