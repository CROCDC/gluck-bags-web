"""Admin section "Textos": edit every string the public site renders.

Mounted at /admin/content, behind the same shared-password session as the products
admin but deliberately kept as its own blueprint, templates and stylesheet — the two
sections share nothing but the login and the chrome.

The flow is draft -> preview -> publish:

    save     writes the pending draft (the live site does not change)
    preview  renders the REAL public page with `?preview=1`, drafts applied
    publish  promotes the pending drafts to the live site

Nothing here knows which strings exist: the screens are generated from
app/content/registry.py, so new copy shows up automatically.
"""

from __future__ import annotations

from typing import Any

from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app import content
from app.auth import login_required
from app.content import registry
from app.content.sanitizer import safe_href, sanitize, strip_tags, visible_text
from app.repositories import SiteTextRepository

content_bp = Blueprint("admin_content", __name__, url_prefix="/admin/content")

# The device frames offered by the preview, in the order they are shown.
PREVIEW_DEVICES: tuple[dict[str, Any], ...] = (
    {"key": "mobile", "label": "Celular", "width": 390, "height": 844},
    {"key": "tablet", "label": "Tablet", "width": 768, "height": 1024},
    {"key": "desktop", "label": "Escritorio", "width": 1280, "height": 800},
)

# The "formats" that are not a viewport but a card rendered from the page's own
# metadata (built client-side from the previewed document — see admin-content.js).
PREVIEW_CARDS: tuple[dict[str, str], ...] = (
    {"key": "google", "label": "Google"},
    {"key": "whatsapp", "label": "WhatsApp"},
    {"key": "twitter", "label": "Twitter/X"},
)


# --- helpers -----------------------------------------------------------------


# An authenticated caller could post 20k unknown keys and get ~2 MB of Spanish back.
MAX_ERRORS = 20


def _add_error(errors: list[str], keys: list[str], message: str, key: str) -> None:
    if len(errors) < MAX_ERRORS:
        errors.append(message)
        keys.append(key)


def _group_or_404(group_key: str) -> registry.Group:
    group = registry.group_for(group_key)
    if group is None:
        # A bare abort(404) renders the PUBLIC 404 — storefront header, "Ver los
        # bolsos" and no way back into the admin.
        abort(
            Response(
                render_template("admin/content/not_found.html", group_key=group_key),
                status=404,
            )
        )
    return group


def _normalize(field: registry.TextField, raw: str) -> str:
    """Whitespace normalization shared by every field type.

    Textareas post CRLF; storing that would make a value differ from its identical
    default and show up as a phantom "edited" field forever.
    """
    # A stored value must never carry the resolver's edit markers: one could forge a
    # second <ct-t> wrapper pointing at another key, and the private-use codepoints
    # shipped to public visitors as tofu.
    from app.content.resolver import _strip_markers

    value = _strip_markers(raw).replace("\r\n", "\n").replace("\r", "\n")
    if field.type in ("line", "url"):
        value = " ".join(value.split())
    else:
        value = "\n".join(line.rstrip() for line in value.split("\n")).strip()
    return value


def _sanitize_loss(original: str, cleaned: str) -> str | None:
    """Reject a save where sanitizing swallowed most of the visible text.

    An unterminated `<script>`/`<svg>` takes the rest of the value with it, and the
    result is what gets PERSISTED — so the page silently loses its content and the
    editor is told everything went fine. Better to refuse and say so.
    """
    # visible_text, not strip_tags: the latter sanitizes on the way through, so both
    # sides measured the same post-sanitize string and the check could never fire.
    before = len(visible_text(original))
    after = len(visible_text(cleaned))
    # Legitimate sanitizing loses almost no VISIBLE text: an unknown tag is dropped
    # but its text is kept. A real loss means a `<script>`/`<svg>` swallowed copy.
    if after < before - 20:
        return (
            "el formato quedó mal (puede haber una etiqueta sin cerrar) y se perdería "
            "buena parte del texto. Revisalo y probá de nuevo."
        )
    return None


def _name(field: registry.TextField) -> str:
    """How to name a field to someone who has to go and fix it.

    Twelve fields are labelled "Antetítulo" and eleven "Descripción en Google", so the
    label alone does not say WHICH one was rejected. The screen and the card are already
    known here — the panel prints exactly this under every label.
    """
    group = registry.GROUPS_BY_KEY[registry.FIELD_GROUP[field.key]]
    section = registry.FIELD_SECTION.get(field.key, "")
    where = f"{group.title} · {section}" if section and section != group.title else group.title
    return f"«{field.label}», en {where}"


def _token_list(key: str) -> str:
    """The placeholders this field accepts, written the way they are typed."""
    return ", ".join("{" + name + "}" for name in registry.allowed_tokens(key))


def _token_error(field: registry.TextField, value: str) -> str | None:
    """Reject a placeholder nothing will ever fill.

    The editor TEACHES braces ("dejá los {textos entre llaves} tal cual") and the panel
    labels the brand field "Marca", so translating `{brand}` to `{marca}` is the natural
    move — and it used to publish a literal `{marca}` into the <h1>. Nothing downstream
    can catch it: the resolver leaves an unknown token alone on purpose, so that a stray
    brace can never raise mid-render.
    """
    unknown = content.unknown_tokens(field.key, value)
    if unknown:
        names = ", ".join("{" + name + "}" for name in unknown[:3])
        return (
            f"{_name(field)}: no conocemos {names}. Los textos entre llaves que "
            f"podés usar en este campo son: {_token_list(field.key)}."
        )
    if content.has_stray_brace(value):
        return (
            f"{_name(field)}: las llaves están reservadas para los textos que se "
            f"completan solos ({_token_list(field.key)}), así que no se pueden usar "
            f"sueltas. Sacalas y guardá de nuevo."
        )
    return None


def _validate(field: registry.TextField, value: str) -> str | None:
    """Return an error message for `value`, or None when it is acceptable."""
    if len(value) > field.max_length:
        return f"{_name(field)}: máximo {field.max_length} caracteres (escribiste {len(value)})."
    if not value:
        # Every type. A blank `text` shipped an empty <h1> and an empty meta
        # description; a blank `lines` emptied the marquee — all in one click, and
        # the guard existed only for the two types that happened to be tested.
        return f"{_name(field)}: no puede quedar vacío."
    token_error = _token_error(field, value)
    if token_error:
        return token_error
    if field.type == "rich" and not strip_tags(value).strip():
        # `<p></p>` and friends: markup with nothing in it reads as a blank page.
        return f"{_name(field)}: quedó sin texto."
    if field.type == "url" and value:
        cleaned = safe_href(value)
        if cleaned is None or not cleaned.lower().startswith(("http://", "https://")):
            return f"{_name(field)}: tiene que ser un link que empiece con https://."
        if cleaned != value:
            # safe_href strips whitespace to defeat `java\tscript:`; storing the
            # original meant a pasted URL with a space became a 404 on every page.
            return f"{_name(field)}: el link tiene espacios o caracteres raros."
    return None


def _apply_submission(group: registry.Group, form: Any) -> tuple[list[str], int]:
    """Stage the posted values as drafts. Returns (errors, number of fields staged).

    A value equal to what is already live clears the draft instead of storing a
    no-op, so the "sin publicar" counter only ever counts real pending changes.
    """
    errors: list[str] = []
    staged = 0
    restore_key = (form.get("restore") or "").strip()

    for field in group.fields:
        if field.key not in form and field.key != restore_key:
            continue
        if field.key == restore_key:
            value = field.default
        else:
            value = _normalize(field, form.get(field.key, ""))
        error = None
        if field.type == "rich":
            # Sanitize BEFORE validating: it re-escapes &, < and >, so it grows the
            # string. Validating first let a value be stored over its own cap, after
            # which the same screen refused to save what it was displaying.
            cleaned = sanitize(value)
            loss = _sanitize_loss(value, cleaned)
            if loss:
                error = f"{_name(field)}: {loss}"
            value = cleaned
        error = error or _validate(field, value)
        if error:
            errors.append(error)
            continue
        state = content.field_state(field.key)
        SiteTextRepository.set_draft(field.key, None if value == state["live"] else value)
        staged += 1

    return errors, staged


def _editor_values(group: registry.Group, form: Any | None = None) -> dict[str, Any]:
    """Per-field state for the editor; `form` (a rejected submission) wins so the
    editor never loses what was just typed."""
    states = content.group_states(group)
    for field in group.fields:
        state = states[field.key]
        if form is not None and field.key in form:
            state = dict(state, value=_normalize(field, form.get(field.key, "")))
        # What the on-screen filter matches against. Rich fields contribute their
        # visible text, not their markup, so searching "Instagram" doesn't hit every
        # paragraph that merely links to it.
        haystack = state["value"]
        if field.type == "rich":
            haystack = strip_tags(haystack)
        state = dict(state, search_text=f"{field.label} {field.key} {haystack}".lower())
        states[field.key] = state
    return states


def _safe_start_path(raw: str | None) -> str:
    """The canvas may only be pointed at a local page of THIS site.

    It used to be rendered straight into the iframe's `src`, so
    `?path=javascript:alert(1)` executed in the admin's own origin, and
    `?path=https://evil.test` embedded a foreign origin inside the admin chrome —
    one link to the shop owner away from acting with her session.
    """
    candidate = (raw or "").strip()
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return "/"
    path = candidate.split("?", 1)[0].split("#", 1)[0]
    if any(page["path"] == path for page in editor_pages()):
        return path
    # Anything else that resolves to a real GET route on this app is fine too.
    from flask import current_app

    adapter = current_app.url_map.bind("localhost")
    try:
        adapter.match(path, method="GET")
    except Exception:  # noqa: BLE001 — unknown path, fall back to the home
        return "/"
    return path


def editor_pages() -> list[dict[str, str]]:
    """The pages the visual editor can jump to, in the order a shop owner thinks.

    Clicking a link inside the canvas is ambiguous (is that a click or an edit?), so
    moving around the site is an explicit picker instead.
    """
    from app.routes import STATIC_PAGES
    from app.services import catalog
    from app.utils import slugify

    pages: list[dict[str, str]] = [{"path": "/", "label": "Inicio"}]

    published = {slugify(name): name for name in catalog.published_categories()}
    for slug in registry.CURATED_CATEGORY_SLUGS:
        label = content.category_label(published.get(slug, slug))
        pages.append({"path": f"/categoria/{slug}", "label": f"Categoría · {label}"})

    products = catalog.get_published()
    if products:
        pages.append(
            {"path": f"/producto/{products[0].id}", "label": f"Producto · {products[0].title}"}
        )

    pages.append({"path": "/carrito", "label": "Carrito"})
    pages.append({"path": "/gracias", "label": "Gracias por tu compra"})
    for slug, page_key in STATIC_PAGES.items():
        pages.append({"path": f"/{slug}", "label": str(content.t(f"page.{page_key}.title"))})
    pages.append({"path": "/404", "label": "Página no encontrada"})
    return pages


def field_payload(key: str) -> dict[str, Any]:
    """The same shape the in-page manifest uses, for one key.

    The visual editor's panel used to only know about the current page, so a pending
    edit made elsewhere was invisible — yet still counted, still published, and if it
    was invalid it blocked every save with nothing to click.
    """
    field = registry.FIELDS[key]
    state = content.field_state(key)
    group = registry.GROUPS_BY_KEY[registry.FIELD_GROUP[key]]
    return {
        "raw": state["value"],
        "type": field.type,
        "label": field.label,
        "hint": field.hint,
        "max": field.max_length,
        "default": field.default,
        "previous": state["previous"],
        # What the shop currently shows. `raw` is the draft once there is one, so without
        # this the panel had no way back to live except the factory text.
        "live": state["live"],
        "group": group.key,
        "groupTitle": group.title,
        "section": registry.FIELD_SECTION.get(key, ""),
        "hasDraft": state["has_draft"],
        "isOverridden": state["is_overridden"],
    }


def pending_payload() -> dict[str, Any]:
    """Everything with a pending draft right now, ready for the panel."""
    keys = [key for key in SiteTextRepository.draft_keys() if key in registry.FIELDS]
    return {"pendingKeys": keys, "pendingFields": {key: field_payload(key) for key in keys}}


def _invalid_drafts(keys: list[str]) -> dict[str, str]:
    """The pending drafts among `keys` that today's rules would refuse.

    Publishing does not re-run the save-time checks, and it publishes drafts this
    request never wrote: one parked by a colleague, or one that predates a rule.
    Re-checking is one `_validate` per pending key — there are almost never more than
    a handful.
    """
    wanted = set(keys)
    problems: dict[str, str] = {}
    for key in SiteTextRepository.draft_keys():
        field = registry.field_for(key)
        if key not in wanted or field is None:
            continue
        row = SiteTextRepository.get(key)
        if row is None or row.draft_value is None:
            continue
        error = _validate(field, row.draft_value)
        if error:
            problems[key] = error
    return problems


def _preview_path(group: registry.Group) -> str:
    """The public URL the preview shows for this group.

    Product and category previews depend on live data (ids, which categories have
    stock), so they're resolved here instead of hardcoded in the registry.
    """
    if group.preview_kind == "product":
        from app.services import catalog

        products = catalog.get_published()
        if products:
            return url_for("product_detail", product_id=products[0].id)
        return "/"
    if group.preview_kind == "category":
        from app.services import catalog
        from app.utils import slugify

        published = {slugify(name) for name in catalog.published_categories()}
        for slug in registry.CURATED_CATEGORY_SLUGS:
            if slug in published:
                return url_for("category_page", slug=slug)
        return url_for("category_page", slug=registry.CURATED_CATEGORY_SLUGS[0])
    return group.preview_path


# --- routes ------------------------------------------------------------------


@content_bp.route("/")
@login_required
def editor() -> str:
    """The visual editor: the live site in a frame, edited in place."""
    return render_template(
        "admin/content/editor.html",
        devices=PREVIEW_DEVICES,
        pages=editor_pages(),
        start_path=_safe_start_path(request.args.get("path")),
        pending=content.pending_draft_count(),
        pending_state=pending_payload(),
    )


@content_bp.route("/list")
@login_required
def index() -> str:
    groups = registry.groups_by_category()
    stats = {
        group.key: {
            "pending": content.pending_draft_count(group),
            "overridden": content.override_count(group),
            "total": len(group.fields),
        }
        for group in registry.GROUPS
    }
    return render_template(
        "admin/content/index.html",
        groups_by_category=groups,
        stats=stats,
        pending_total=content.pending_draft_count(),
    )


def _flash_count(count: int, singular: str, plural: str) -> None:
    """Report how many texts an action touched (or that there was nothing to do)."""
    if count:
        flash(singular.format(n=count) if count == 1 else plural.format(n=count), "success")
    else:
        flash("No había cambios sin publicar.", "notice")


@content_bp.route("/save", methods=["POST"])
@login_required
def save_changes() -> Any:
    """Stage (and optionally publish) the edits made in the visual editor.

    All-or-nothing, like the form editor: if any field is rejected nothing is
    written, and the editor keeps the changes so nothing typed is lost.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("changes"), dict):
        return {"ok": False, "errors": ["No pudimos leer los cambios."]}, 400

    errors: list[str] = []
    error_keys: list[str] = []
    staged = 0
    for raw_key, raw_value in list(data["changes"].items())[:MAX_ERRORS * 5]:
        field = registry.field_for(str(raw_key))
        if field is None:
            _add_error(errors, error_keys, f"Uno de los textos ya no existe ({raw_key}). Recargá el editor.", str(raw_key))
            continue
        if not isinstance(raw_value, str):
            # JSON hands us lists, dicts, numbers and booleans; str() turned them into
            # their Python repr and published `['uno', 'dos']` as the site's <h1>.
            _add_error(errors, error_keys, f"{_name(field)}: no pudimos leer ese texto.", field.key)
            continue
        value = _normalize(field, raw_value)
        error = None
        if field.type == "rich":
            cleaned = sanitize(value)
            loss = _sanitize_loss(value, cleaned)
            if loss:
                error = f"{_name(field)}: {loss}"
            value = cleaned
        error = error or _validate(field, value)
        if error:
            # The editor needs the key, not just the message: it highlights the
            # offending text on the page and scrolls the panel to it.
            _add_error(errors, error_keys, error, field.key)
            continue
        state = content.field_state(field.key)
        SiteTextRepository.set_draft(field.key, None if value == state["live"] else value)
        staged += 1

    if errors:
        SiteTextRepository.rollback()
        return {"ok": False, "errors": errors, "errorKeys": error_keys}, 400

    published = 0
    if data.get("action") == "publish":
        # Only the keys the editor is showing as pending. This used to publish every
        # draft in the database, so a colleague's half-finished text — or something
        # parked days ago — went live with the confirm never naming it.
        requested = data.get("keys")
        # Absent or malformed means "nothing", never "everything": the whole point is
        # that a colleague's parked draft does not ride along. De-duplicated because
        # the list goes straight into an SQL IN (…), which 500s past ~32k entries.
        scope = (
            sorted({str(key) for key in requested if str(key) in registry.FIELDS})
            if isinstance(requested, list)
            else []
        )
        # A published key here may be someone else's parked draft, which this request
        # never validated.
        problems = _invalid_drafts(scope)
        if problems:
            SiteTextRepository.rollback()
            return {
                "ok": False,
                "errors": list(problems.values())[:MAX_ERRORS],
                "errorKeys": list(problems)[:MAX_ERRORS],
            }, 400
        published = SiteTextRepository.publish(scope, registry.DEFAULTS)
    SiteTextRepository.save()
    return {
        "ok": True,
        "saved": staged,
        "published": published,
        "pending": content.pending_draft_count(),
        **pending_payload(),
    }


@content_bp.route("/revert", methods=["POST"])
@login_required
def revert() -> Any:
    """Stage the wording that was live before the last publish, as a DRAFT.

    It used to publish on the spot, which made one underlined link in the panel the
    only control in the whole editor that changed the public site without going
    through "Publicar cambios" — sitting next to an identical-looking link that only
    drafted. Now there is a single rule with no exceptions. It is also idempotent,
    where the old in-place swap meant a double click published and unpublished.

    Going back a step still works: `publish` records what it replaced, so drafting the
    previous wording and publishing it leaves exactly the same pair of values the old
    swap did.
    """
    data = request.get_json(silent=True) or {}
    raw = data.get("keys") if isinstance(data.get("keys"), list) else [data.get("key")]
    keys = [str(item) for item in raw if str(item) in registry.FIELDS]
    if not keys:
        return {"ok": False, "errors": ["Ese texto no existe."]}, 400

    values: dict[str, str] = {}
    for key in dict.fromkeys(keys):
        state = content.field_state(key)
        if state["has_previous"]:
            target = state["previous"]
        elif state["previous"] is None and state["is_overridden"]:
            # `previous_value` is NULL both when a key was never published and when
            # what it replaced WAS the registry default (publish stores "no override"
            # as NULL). Either way the step back is the default — which is what
            # "Volver al texto original" already did under a name nobody reads as
            # "undo". Spelled out here so one Deshacer covers every key at once.
            target = registry.FIELDS[key].default
        else:
            continue
        if target is None or target == state["live"]:
            continue
        # A draft parked here would quietly win the next publish over the undo.
        SiteTextRepository.set_draft(key, target)
        values[key] = target

    if not values:
        return {"ok": False, "errors": ["No hay una versión anterior de este texto."]}, 400
    SiteTextRepository.save()
    return {"ok": True, "values": values, **pending_payload()}


@content_bp.route("/publish", methods=["POST"])
@login_required
def publish_all() -> Response:
    problems = _invalid_drafts(list(registry.FIELDS))
    if problems:
        for message in problems.values():
            flash(message, "error")
        flash("No publicamos nada: hay borradores con errores.", "error")
        return redirect(url_for("admin_content.index"))
    changed = SiteTextRepository.publish(list(registry.FIELDS), registry.DEFAULTS)
    SiteTextRepository.save()
    _flash_count(changed, "Se publicó {n} texto.", "Se publicaron {n} textos.")
    return redirect(url_for("admin_content.index"))


@content_bp.route("/discard", methods=["POST"])
@login_required
def discard_all() -> Response:
    dropped = SiteTextRepository.discard_drafts(list(registry.FIELDS))
    SiteTextRepository.save()
    _flash_count(
        dropped, "Se descartó {n} cambio sin publicar.", "Se descartaron {n} cambios sin publicar."
    )
    return redirect(url_for("admin_content.index"))


@content_bp.route("/<group_key>")
@login_required
def group_edit(group_key: str) -> str:
    group = _group_or_404(group_key)
    return render_template(
        "admin/content/group.html",
        group=group,
        states=_editor_values(group),
        preview_path=_preview_path(group),
        pending=content.pending_draft_count(group),
    )


@content_bp.route("/<group_key>", methods=["POST"])
@login_required
def group_save(group_key: str) -> Any:
    group = _group_or_404(group_key)
    action = request.form.get("action", "save")

    if action == "discard":
        dropped = SiteTextRepository.discard_drafts([f.key for f in group.fields])
        SiteTextRepository.save()
        _flash_count(dropped, "Se descartó {n} cambio.", "Se descartaron {n} cambios.")
        return redirect(url_for("admin_content.group_edit", group_key=group.key))

    errors, _staged = _apply_submission(group, request.form)
    if errors:
        # Nothing is written when anything failed: a half-saved screen is worse than
        # a rejected one.
        SiteTextRepository.rollback()
        for message in errors:
            flash(message, "error")
        return (
            render_template(
                "admin/content/group.html",
                group=group,
                states=_editor_values(group, request.form),
                preview_path=_preview_path(group),
                pending=content.pending_draft_count(group),
            ),
            400,
        )

    if action == "publish":
        keys = [f.key for f in group.fields]
        problems = _invalid_drafts(keys)
        if problems:
            # Keep what was just typed (it validated); only the publish is refused.
            SiteTextRepository.save()
            for message in problems.values():
                flash(message, "error")
            flash("No publicamos nada: hay borradores con errores.", "error")
            return redirect(url_for("admin_content.group_edit", group_key=group.key))
        SiteTextRepository.publish(keys, registry.DEFAULTS)
        SiteTextRepository.save()
        flash("Cambios publicados. Ya se ven en la web.", "success")
    else:
        SiteTextRepository.save()
        if request.form.get("restore"):
            flash("Listo, volvió al texto original. Publicá para que se vea en la web.", "success")
        else:
            flash("Borrador guardado. Previsualizá y publicá cuando quieras.", "success")
    return redirect(url_for("admin_content.group_edit", group_key=group.key))


@content_bp.route("/<group_key>/preview")
@login_required
def group_preview(group_key: str) -> str:
    group = _group_or_404(group_key)
    return render_template(
        "admin/content/preview.html",
        group=group,
        preview_path=_preview_path(group),
        devices=PREVIEW_DEVICES,
        cards=PREVIEW_CARDS,
        pending=content.pending_draft_count(group),
    )


def register_admin_content(app: Flask) -> None:
    app.register_blueprint(content_bp)
