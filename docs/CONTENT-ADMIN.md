# Editable site copy — the admin "Textos" section

Every user-facing string on the public site is editable from `/admin/content`,
without a deploy. This document explains how that works and how to extend it.

**TL;DR for adding new copy:** add one `TextField` to `app/content/registry.py`, call
`t('<its key>')` in the template. Nothing else — no migration, no seed, no admin form
to touch, no test to write for the plumbing.

---

## 1. The model: registry + overrides

The database stores **overrides only**. The copy itself lives in code.

```
app/content/registry.py   the catalogue: every editable string, with its default
site_texts (table)        one row per OVERRIDDEN string
app/content/resolver.py   default -> published override -> (preview) draft
```

Resolution order, lowest priority first:

| source | when it wins |
|--------|--------------|
| `TextField.default` (code) | always, unless overridden |
| `site_texts.published_value` | whenever the row exists |
| `site_texts.draft_value` | only in preview mode, for a logged-in admin |

Consequences worth knowing:

- A fresh database renders **exactly** what the code says. There is no seeding step
  and no "the staging site says something different" class of bug.
- "Restore the original" is a row delete. No residue, no stale flag.
- Adding a string never needs a data migration.

### Keys are an API

A key (`home.hero.title`) is the primary key of the override row. **Renaming a key
silently drops whatever the shop wrote there.** If you must rename one, migrate the
row.

---

## 2. Field types

| type | widget | rendering | use for |
|------|--------|-----------|---------|
| `line` | input | escaped | titles, labels, buttons, aria-labels |
| `text` | textarea | escaped | paragraphs |
| `lines`| textarea | list, one item per line | bullet lists, the marquee |
| `rich` | textarea | allow-list sanitized HTML | editorial/legal page bodies |
| `url`  | input | validated `http(s)` link | Instagram, external links |

`rich` accepts only `p h2 h3 ul ol li strong b em i a br`. Everything else is
stripped (tags dropped, their text kept); `script`/`style`/`iframe`/`svg` are dropped
with their content. See `app/content/sanitizer.py`.

Rich values are sanitized **on save and on render**. The second pass is deliberate:
a value that reached the table some other way (a restored backup, a manual `UPDATE`)
must not be able to inject script into a public page. `url` values are re-checked on
render for the same reason, falling back to the registry default.

---

## 3. Tokens

Any string may embed `{token}`. Unknown tokens are left literal — an editor typing a
stray brace never raises.

Always available: `{brand}`, `{tagline}`, `{year}`, `{instagram_url}`,
`{instagram_handle}`.

Per-call, passed by the template or route:

```jinja
{{ t('category.meta.title', category=category_display) }}
{{ t('product.gallery.thumb_aria', index=loop.index, total=product.media|length) }}
```

Tokens are interpolated **before** sanitizing, so a token's value is treated as data
(escaped), never as markup.

---

## 4. The visual editor

`/admin/content/` is the front door: the live site in a frame, edited in place.

- **Click any text on the page and type over it.** Saving and publishing happen from
  the toolbar; nothing is live until you publish.
- **Copy with no visible text** — the `<title>`, the meta description, image `alt`s,
  aria-labels — is in the side panel ("Textos ocultos"). Clicking an image opens its
  alt text there.
- **Page picker** in the toolbar moves around the site. Clicking a link inside the
  canvas would be ambiguous (edit, or navigate?), so it is explicit. Unsaved edits
  travel with you.
- **Device widths** (Auto / Celular / Tablet / Escritorio) and the **share/search
  cards** are the same formats the group preview offers.
- `/admin/content/list` is the same copy as a list of forms — how you find one
  specific string, and the path that works with JavaScript off.

### How a click maps back to a field

In edit mode the resolver wraps every value it returns in private-use markers
carrying its key (`resolver.editable`). A response hook
(`app/content/editor_markup.py`) rewrites those, once per response:

| where the value landed | becomes |
|------------------------|---------|
| visible text | `<ct-t data-k="key">…</ct-t>`, click-to-edit |
| an attribute (`alt`, `aria-label`, `content`) | stripped; the key is recorded on the element as `data-ct-keys` (badge → panel) |
| `<title>` / `<script>` / `<style>` | stripped; the key goes to the panel |

Plus a JSON manifest of every field on the page (raw value, type, label, limits).

Two consequences worth keeping in mind:

- **New copy is covered automatically.** A new registry entry is editable in the
  visual editor with no extra work — nothing is annotated by hand.
- **The public render is untouched.** Markers only exist when a logged-in admin asks
  for `?edit=1`; `tests/test_editor_markup.py` asserts no marker, wrapper or manifest
  can appear on a normal response.

### What is edited where

Short copy — a button label, a heading, a bullet — is edited **in place** on the page.
That is where in-place editing earns its keep.

A **full editorial page body** (the six `/nosotras`-style pages) opens its own editor
instead: a readable column, a toolbar in words rather than symbols, and a count of the
characters a reader actually sees. Editing a page of `<p>`/`<h2>` inside a floating box
over the site is where every reviewer got hurt — the toolbar and the counter rendered
outside the canvas on a phone, the caret fought the raw-value swap, and one select-all
could blank the page and publish it. WordPress does not inline-edit a post body in the
theme either.

The side panel routes those bodies to the same editor rather than showing raw HTML in
a six-row textarea, where one deleted `</p>` breaks the page.

### Editing on a live page

The page under the canvas stays a working site: clicking the cart, the menu or the
gallery arrows behaves normally, because the copy inside those panels only exists
once they are open. Only a click that lands on editable text is intercepted.

- Short copy is selected on focus so typing replaces it; a `rich` body is not —
  there, one keystroke would wipe the page, so the caret just lands where you clicked.
- The bubble under the text carries the hint, the token warning and a live character
  count, so the limit is visible before you save rather than after.
- `Escape` cancels the edit and nothing else (the site closes its drawer on Escape
  too, and losing the panel you were editing inside would be worse).
- Emptying a text keeps a marked placeholder box; a truly empty inline node collapses
  to 0×0 and there would be nothing left to click.
- Clicking text that is NOT editable says where it does live. It only blames the
  product catalogue when the click actually landed in product markup — the generic
  version sent people hunting in a section that had nothing to do with the text.
- A click on a control the SITE owns (the cart, the menu, a gallery arrow) passes
  through. The copy inside those panels only exists once they are open.

### Tokens are edited raw

Clicking a value that contains `{brand}` swaps the rendered text for the raw string,
so typing over it can't silently bake the brand name into that field. The editor
shows a hint, and the rendered text comes back on blur.

### Values that are serialized, not rendered

The cart-drawer strings ship as inline JSON for `cart.js`, and the JSON-LD is built
in Python. Both use `t_plain()` / `content.t()` — the marker-free variants — because
a marker inside `json.dumps` output would survive as literal `\uXXXX` text.

---

## 5. Draft → preview → publish

```
Guardar borrador   writes draft_value; the live site does not change
Previsualizar      opens the REAL page with ?preview=1, drafts applied
Guardar y publicar promotes drafts to published_value
Restaurar original drafts the registry default (publish it to remove the override)
```

A saved value equal to what is already live clears the draft instead of storing a
no-op, so the "sin publicar" counter only ever counts real pending changes.

Publishing records the wording it replaced (`previous_value`), so a published mistake
has a way back that is not "retype what you remember": the panel offers **Volver al
texto anterior** next to **Restaurar original** (the factory text).

One consequence worth knowing: a key that has ever been published keeps its row even
after it goes back to the default, because that row is now the history. "Not
overridden" is no longer the same as "no row". It is bounded — at most one small row
per registry key — and `published_value IS NULL` remains the single source of truth
for "renders the default".

Publishing from the visual editor publishes **the keys that editor is holding**, and
the confirm names them. It used to publish every draft in the database, so a
colleague's half-finished text went live under a confirm that never mentioned it.

### Preview mode

`?preview=1` on any public URL switches the resolver to drafts — **only when the
request carries an admin session**. From the public it is a no-op, so unpublished
copy can never leak through a shared link. Preview responses carry
`X-Robots-Tag: noindex, nofollow` and `Cache-Control: no-store`.

The preview screen offers six formats:

- **Celular / Tablet / Escritorio** — the real page in an iframe at 390 / 768 / 1280
  CSS px, scaled to fit. Media queries see the true viewport.
- **Google / WhatsApp / Twitter-X** — SERP and social cards built client-side from
  the previewed document's own `<title>` and `meta` tags. There is no second
  implementation of the meta logic to drift out of sync: the card shows literally
  what the page emits.

---

## 6. Adding a new editable string

1. Pick a group in `app/content/registry.py` (or add one) and append a `TextField`:

```python
TextField(
    "home.hero.badge",          # stable key, dotted
    "Etiqueta del hero",        # what the editor sees (Spanish)
    "Envío gratis",             # the current copy, verbatim
    type="line",
    hint="Se muestra arriba del título.",
)
```

2. Render it:

```jinja
<span class="hero-badge">{{ t('home.hero.badge') }}</span>
```

That is the whole change. The admin screen, the counters, the restore button, the
preview and the publish flow all pick it up automatically.

`tests/test_content_registry.py` enforces the contract: unique keys, non-empty
defaults that fit their own `max_length`, rich defaults that survive the sanitizer,
no key referenced by a template that isn't declared, and no declared key that nothing
renders.

### Adding a new editorial page

Add a `_editorial_page(...)` group in the registry and one row in `STATIC_PAGES`
(`app/routes.py`). All six existing pages share one template
(`templates/pages/_page_base.html`) — there is no per-page template to write.

---

## 7. What is deliberately NOT editable

**A curated category's canonical name** (`Tote`, `Mini Bag`, `Bucket Bag`, `Clutch`
in `app/routes.py`). It is identity, not copy: it is the URL slug
(`/categoria/mini-bag`, which carries pre-migration ranking history) and the value
stored on every product row. Renaming it from the admin would 404 indexed URLs and
orphan products.

Its **display label** is editable (`category.<slug>.label`) and is used everywhere the
name is shown — cards, headings, breadcrumbs, chips and the matching JSON-LD — while
the slug stays put.

**Product titles, descriptions and prices** are catalogue data, not site copy. They
live in Tienda Nube (or the products admin under `CATALOG_SOURCE=admin`).

---

## 8. Performance

The resolver reads the overrides **once per request** (a single `SELECT` over a table
with one row per overridden string) and caches the result on `flask.g`. It is not
cached in the process on purpose: several gunicorn workers share one SQLite file, so
a process-level cache would keep serving stale copy in the other workers after an
edit, with no way to invalidate across processes.

Writes call `SiteTextRepository.save()`, which drops that request's snapshot.

A database failure degrades to the registry defaults rather than raising: copy must
never be able to take the site down.

---

## 9. Layout

```
app/content/__init__.py        public surface: t, t_lines, register_content, …
app/content/registry.py        the catalogue (Group -> Section -> TextField)
app/content/resolver.py        resolution, tokens, preview mode, Jinja wiring
app/content/sanitizer.py       allow-list HTML sanitizer
app/models/site_text.py        the overrides table
app/repositories/site_text_repository.py
app/content/editor_markup.py   edit-mode markers -> editable markup + manifest
app/admin_content/__init__.py  the /admin/content blueprint
app/templates/admin/content/   editor / index / group / preview screens
app/static/css/admin-content.css   list + group screens
app/static/js/admin-content.js     counters, filter, preview formats
app/static/css/admin-editor.css    the visual editor shell
app/static/js/admin-editor.js      pending changes, saving, panel, devices
app/static/css/editor-frame.css    injected into the page being edited
app/static/js/editor-frame.js      click-to-edit inside that page
```

Tests: `test_content_sanitizer`, `test_content_registry`, `test_content_resolver`,
`test_site_text_repository`, `test_admin_content_routes`, `test_content_public_render`,
`test_content_security`, `test_editor_markup`, `test_visual_editor_api`,
`test_e2e_admin_content`, `test_e2e_visual_editor`, plus the four new screens in
`test_responsive`.
