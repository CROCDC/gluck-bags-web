"""End-to-end browser flow for the visual editor (/admin/content/).

This is the WordPress-style path: the live site inside a frame, click a text, type
over it, publish. None of that can be verified without a real browser — the wiring
is postMessage between two documents, contenteditable and a fetch — so it is all
driven here through actual clicks and keystrokes.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e

DEFAULT_TIMEOUT_MS = 20_000

# Needs a product on the storefront, which under CATALOG_SOURCE=tiendanube comes from
# the (empty) TN mirror rather than the admin catalogue.
needs_admin_catalogue = pytest.mark.skipif(
    os.environ.get("TEST_CATALOG_SOURCE", "admin") != "admin",
    reason="necesita el catálogo admin para tener una ficha de producto",
)

HERO = "home.hero.subtitle"
HERO_ORIGINAL = "Bolsos de líneas puras, pensados para durar. Sin pieles, sin excesos."


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    # reduced_motion freezes the hero/marquee animations; without it Playwright
    # waits forever for an "unstable" element before clicking.
    context = browser.new_context(
        viewport={"width": 1440, "height": 950}, reduced_motion="reduce"
    )
    pg = context.new_page()
    pg.set_default_timeout(DEFAULT_TIMEOUT_MS)
    pg.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
    # The editor confirms before publishing and warns before leaving with unsaved
    # changes; accept both so a test never hangs on a dialog.
    pg.on("dialog", lambda dialog: dialog.accept())
    try:
        yield pg
    finally:
        context.close()


@pytest.fixture
def restore(page: Page, admin_live_server: tuple[str, str]):
    """Put every key a test touched back to its registry default afterwards."""
    base_url, _password = admin_live_server
    touched: list[str] = []
    yield touched.append
    if touched:
        from app.content import registry

        page.request.post(
            f"{base_url}/admin/content/save",
            data={
                "changes": {key: registry.DEFAULTS[key] for key in touched},
                "action": "publish",
            },
        )


def _open_editor(page: Page, base_url: str, password: str, path: str = "/") -> None:
    page.goto(f"{base_url}/admin/login", wait_until="load")
    if page.locator('input[name="password"]').count():
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_url(re.compile(r"/admin/?$"))
    page.goto(f"{base_url}/admin/content/?path={path}", wait_until="load")
    # Wait for the frame to announce itself (the shell renders the panel from it).
    expect(page.locator("[data-ed-hidden-count]")).not_to_have_text("0")


def _type_over(page: Page, key: str, text: str) -> None:
    node = page.frame_locator("[data-ed-iframe]").locator(f'ct-t[data-k="{key}"]').first
    node.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type(text)
    page.keyboard.press("Enter")


def _public_html(browser: Browser, base_url: str, path: str = "/") -> str:
    """The page as an anonymous visitor sees it."""
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(base_url.rstrip("/") + path, wait_until="load")
        return page.content()
    finally:
        context.close()


# --- the main flow -------------------------------------------------------------


def test_click_type_save_publish(
    admin_live_server: tuple[str, str], page: Page, browser: Browser, restore
) -> None:
    base_url, password = admin_live_server
    restore(HERO)
    new_text = "Bolsos hechos para durar (editor visual)"

    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    # 1. Type over the text, in place, on the real page.
    _type_over(page, HERO, new_text)
    expect(frame.locator(".hero-sub")).to_have_text(new_text)
    expect(page.locator("[data-ed-save]")).to_be_enabled()
    expect(page.locator("[data-ed-status]")).to_have_text("Cambios sin guardar")

    # 2. Nothing is live yet.
    assert new_text not in _public_html(browser, base_url)

    # 3. Save the draft: still not live, but it survives a reload of the editor.
    page.click("[data-ed-save]")
    expect(page.locator("[data-ed-status]")).to_contain_text("Borrador guardado")
    assert new_text not in _public_html(browser, base_url)
    page.reload(wait_until="load")
    expect(frame.locator(".hero-sub")).to_have_text(new_text)

    # 4. Publish.
    page.click("[data-ed-publish]")
    expect(page.locator("[data-ed-status]")).to_contain_text("Publicado")
    assert new_text in _public_html(browser, base_url)


def test_the_same_text_updates_everywhere_at_once(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    """A nav label renders in the header, the mobile menu and the footer; editing one
    occurrence has to move all three, because they are one field."""
    base_url, password = admin_live_server
    restore("nav.collection")
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    _type_over(page, "nav.collection", "Nuestra línea")
    nodes = frame.locator('ct-t[data-k="nav.collection"]')
    assert nodes.count() == 3
    for index in range(nodes.count()):
        expect(nodes.nth(index)).to_have_text("Nuestra línea")


def test_a_value_with_a_token_is_edited_raw(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """The hero H1 renders the tagline through `{tagline}`. Clicking it must show the
    token, not the interpolated text — otherwise typing would bake the brand in."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    title = frame.locator('ct-t[data-k="home.hero.title"]')
    expect(title).to_have_text("Bolsos minimalistas de cuero vegano")
    title.click()
    expect(title).to_have_text("{tagline}")
    page.keyboard.press("Escape")
    expect(title).to_have_text("Bolsos minimalistas de cuero vegano")


def test_escape_cancels_an_edit(admin_live_server: tuple[str, str], page: Page) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    node = frame.locator(f'ct-t[data-k="{HERO}"]')
    node.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type("esto no se guarda")
    page.keyboard.press("Escape")

    expect(node).to_have_text(HERO_ORIGINAL)
    expect(page.locator("[data-ed-save]")).to_be_disabled()


def test_a_list_item_edits_only_its_own_line(
    admin_live_server: tuple[str, str], page: Page, browser: Browser, restore
) -> None:
    base_url, password = admin_live_server
    restore("home.feature.specs")
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    items = frame.locator('ct-t[data-k^="home.feature.specs#"]')
    assert items.count() == 3
    original_last = items.nth(2).inner_text()

    items.nth(0).click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type("Primera característica nueva")
    page.keyboard.press("Enter")

    expect(items.nth(0)).to_have_text("Primera característica nueva")
    expect(items.nth(2)).to_have_text(original_last)

    page.click("[data-ed-publish]")
    expect(page.locator("[data-ed-status]")).to_contain_text("Publicado")
    html = _public_html(browser, base_url)
    assert "<li>Primera característica nueva</li>" in html
    assert f"<li>{original_last}</li>" in html


# --- the side panel ------------------------------------------------------------


def test_the_panel_edits_the_copy_that_has_nowhere_to_click(
    admin_live_server: tuple[str, str], page: Page, browser: Browser, restore
) -> None:
    """The <title>, the meta description and the image alts have no visible text, so
    they live in the panel."""
    base_url, password = admin_live_server
    restore("seo.home.title")
    _open_editor(page, base_url, password)

    page.click("[data-ed-panel-toggle]")
    field = page.locator('[data-ed-field="seo.home.title"] input')
    expect(field).to_have_value("{brand} · {tagline}")
    field.fill("Título escrito desde el panel")

    expect(page.locator("[data-ed-save]")).to_be_enabled()
    page.click("[data-ed-publish]")
    expect(page.locator("[data-ed-status]")).to_contain_text("Publicado")
    assert "<title>Título escrito desde el panel</title>" in _public_html(browser, base_url)


def test_clicking_an_image_opens_its_alt_text_in_the_panel(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """An image has no text to click, so the element itself opens its alt in the
    panel. (The hero image is covered by its own overlay, so this uses the feature
    image — the copy is reachable from the panel list either way.)"""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)

    page.frame_locator("[data-ed-iframe]").locator(".feature-media img").click()
    expect(page.locator("[data-ed-panel]")).to_be_visible()
    expect(page.locator('[data-ed-field="home.feature.image_alt"] input')).to_be_focused()


def test_the_panel_and_the_page_stay_in_sync(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    base_url, password = admin_live_server
    restore(HERO)
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    page.click("[data-ed-panel-toggle]")
    page.click('[data-ed-tab="all"]')
    page.fill("[data-ed-filter]", "bajada")
    page.locator(f'[data-ed-field="{HERO}"] textarea, [data-ed-field="{HERO}"] input').first.fill(
        "Escrito en el panel"
    )
    expect(frame.locator(".hero-sub")).to_have_text("Escrito en el panel")


def test_the_share_cards_read_the_edited_page(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    page.click("[data-ed-panel-toggle]")
    page.click('[data-ed-tab="cards"]')

    expect(page.locator("[data-ct-serp-title]")).to_have_text(
        "GLÜCK · Bolsos minimalistas de cuero vegano"
    )
    assert page.locator("[data-ct-serp-desc]").inner_text().strip()
    assert "gluckbags.com" in page.locator("[data-ct-serp-url]").inner_text()
    expect(page.locator("[data-ct-og-image]")).to_be_visible()


# --- moving around the site ----------------------------------------------------


def test_the_page_picker_moves_around_the_site(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Clicking a link inside the canvas is ambiguous (edit or navigate?), so moving
    around is an explicit picker — and editing keeps working on the new page."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    page.select_option("[data-ed-page]", "/nosotras")
    expect(frame.locator("h1.section-title")).to_have_text("Nosotras")
    expect(frame.locator("ct-t").first).to_be_visible()
    # The editorial body is rich text and is listed for this page.
    expect(page.locator('[data-ed-field="page.about.body"]')).to_have_count(1)


def test_pending_edits_survive_changing_pages(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    base_url, password = admin_live_server
    restore("nav.cta")
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    _type_over(page, "nav.cta", "Comprá ya")
    page.select_option("[data-ed-page]", "/carrito")
    expect(frame.locator(".cart-page")).to_be_visible()
    # The header CTA is on every page: the unsaved edit came along.
    expect(frame.locator('ct-t[data-k="nav.cta"]').first).to_have_text("Comprá ya")
    expect(page.locator("[data-ed-save]")).to_be_enabled()


def test_the_device_frames_resize_the_canvas(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.locator("[data-ed-frame]")

    page.click('[data-ed-device="mobile"]')
    assert frame.evaluate("el => el.style.width") == "390px"
    page.click('[data-ed-device="desktop"]')
    assert frame.evaluate("el => el.style.width") == "1280px"
    page.click('[data-ed-device="fluid"]')
    assert frame.evaluate("el => el.style.width") == ""

    # The canvas is scaled to fit, never spilling out of its stage.
    box = frame.bounding_box()
    stage = page.locator("[data-ed-stage]").bounding_box()
    assert box and stage and box["width"] <= stage["width"] + 1


def test_a_rejected_value_is_reported_and_nothing_is_saved(
    admin_live_server: tuple[str, str], page: Page, browser: Browser
) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)

    page.click("[data-ed-panel-toggle]")
    page.locator('[data-ed-field="global.instagram_url"] input').fill("javascript:alert(1)")
    page.click("[data-ed-save]")

    status = page.locator("[data-ed-status]")
    expect(status).to_have_class(re.compile("is-error"))
    expect(status).to_contain_text("link")
    assert "javascript:alert(1)" not in _public_html(browser, base_url)


# --- regressions found by using the editor for real ---------------------------


def test_emptying_a_text_leaves_something_left_to_click(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    """An empty inline node collapses to a 0x0 box: without a placeholder there is
    nothing left to click and no way back short of reloading."""
    base_url, password = admin_live_server
    restore("nav.cta")
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")
    node = frame.locator('ct-t[data-k="nav.cta"]').first

    node.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.press("Delete")
    page.keyboard.press("Enter")

    box = node.bounding_box()
    assert box and box["width"] > 20 and box["height"] > 8, box

    node.click()
    page.keyboard.type("Comprar")
    page.keyboard.press("Enter")
    expect(node).to_have_text("Comprar")


def test_the_site_stays_interactive_so_its_own_panels_can_be_edited(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """The cart drawer's copy only exists once the drawer is open, so the editor must
    not swallow the click that opens it."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    frame.locator(".cart-toggle").click()
    expect(frame.locator("#cartDrawer.open")).to_be_visible()
    drawer_texts = frame.locator("#cartDrawer ct-t")
    assert drawer_texts.count() >= 4
    drawer_texts.first.click()
    expect(frame.locator("ct-t[data-ct-editing]")).to_have_count(1)


def test_escape_cancels_the_edit_without_closing_the_panel_around_it(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    frame.locator(".cart-toggle").click()
    expect(frame.locator("#cartDrawer.open")).to_be_visible()
    frame.locator("#cartDrawer ct-t").first.click()
    page.keyboard.press("Escape")

    expect(frame.locator("ct-t[data-ct-editing]")).to_have_count(0)
    expect(frame.locator("#cartDrawer.open")).to_be_visible()


def test_a_long_body_is_not_selected_whole_when_you_click_into_it(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Select-all makes sense for a button label; on a legal page it means one
    keystroke wipes the page."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password, path="/nosotras")
    frame = page.frame_locator("[data-ed-iframe]")

    frame.locator('ct-t[data-k="page.about.body"]').first.click(position={"x": 60, "y": 12})
    selected = frame.locator("body").evaluate("() => window.getSelection().toString().length")
    assert selected == 0


def test_the_character_count_is_visible_while_typing_in_place(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    """You used to only find out about the limit when the save failed."""
    base_url, password = admin_live_server
    restore(HERO)
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    frame.locator(f'ct-t[data-k="{HERO}"]').first.click()
    tip = frame.locator(".ct-tip").first
    expect(tip).to_contain_text("/ 800 caracteres")

    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.insert_text("x" * 850)
    expect(tip).to_contain_text("850 / 800")
    expect(tip).to_have_class(re.compile("is-over"))


def test_a_rejected_save_points_at_the_offending_text(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    frame.locator(f'ct-t[data-k="{HERO}"]').first.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.insert_text("x" * 850)
    page.keyboard.press("Enter")
    page.click("[data-ed-save]")

    expect(page.locator("[data-ed-status]")).to_have_class(re.compile("is-error"))
    expect(page.locator("[data-ed-panel]")).to_be_visible()
    expect(page.locator(f'[data-ed-field="{HERO}"]')).to_have_class(re.compile("is-invalid"))
    expect(frame.locator(f'ct-t[data-k="{HERO}"][data-ct-invalid]')).to_have_count(1)


@needs_admin_catalogue
def test_clicking_catalogue_text_says_where_to_edit_it(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Product titles and prices are catalogue data. Clicking them used to do nothing
    at all, which reads as a broken editor."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    product_path = next(
        value
        for value in page.locator("[data-ed-page] option").evaluate_all(
            "options => options.map(o => o.value)"
        )
        if value.startswith("/producto/")
    )
    page.select_option("[data-ed-page]", product_path)
    frame = page.frame_locator("[data-ed-iframe]")
    expect(frame.locator(".pdp-title")).to_be_visible()

    frame.locator(".pdp-title").click()
    expect(frame.locator(".ct-tip")).to_contain_text("catálogo")


def test_the_category_cards_are_editable_in_place(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    base_url, password = admin_live_server
    restore("category.tote.label")
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")

    card = frame.locator('.cat-name ct-t[data-k="category.tote.label"]').first
    card.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type("Totes")
    page.keyboard.press("Enter")
    expect(card).to_have_text("Totes")


# --- keyboard access, and the regressions that adding it introduced -------------


def test_enter_commits_an_edit_instead_of_reopening_it(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    """Making the texts focusable meant the element kept focus after Enter, so the
    same Enter bubbled on and reopened the edit with everything selected — the next
    keystroke wiped the field."""
    base_url, password = admin_live_server
    restore("home.hero.eyebrow")
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")
    node = frame.locator('ct-t[data-k="home.hero.eyebrow"]').first

    node.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type("VALOR BUENO")
    page.keyboard.press("Enter")

    expect(frame.locator("ct-t[data-ct-editing]")).to_have_count(0)
    page.keyboard.type("Z")  # must not land in the field
    expect(node).to_have_text("VALOR BUENO")


def test_a_text_can_be_edited_with_the_keyboard_alone(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    base_url, password = admin_live_server
    restore("nav.cta")
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")
    node = frame.locator('ct-t[data-k="nav.cta"]').first

    assert node.get_attribute("tabindex") == "0"
    # A control that opens an editor, not a textbox: 98 fake textboxes made a screen
    # reader offer 98 fields that swallowed keystrokes.
    assert node.get_attribute("role") == "button"
    assert "Editar" in (node.get_attribute("aria-label") or "")

    node.evaluate("el => el.focus()")
    page.keyboard.press("Enter")
    expect(frame.locator("ct-t[data-ct-editing]")).to_have_count(1)
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type("Comprá ya")
    page.keyboard.press("Enter")
    expect(node).to_have_text("Comprá ya")


def test_a_long_page_body_opens_its_own_editor(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Editing a whole editorial page inside a floating box over the site is where
    every reviewer got hurt — the toolbar and the counter rendered outside the canvas,
    the caret fought the raw-value swap, and one select-all could blank the page."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password, path="/nosotras")
    frame = page.frame_locator("[data-ed-iframe]")

    frame.locator('ct-t[data-k="page.about.body"]').first.click()
    expect(page.locator("[data-ed-sheet]")).to_be_visible()
    # …and it is NOT being edited on top of the site.
    expect(frame.locator("ct-t[data-ct-editing]")).to_have_count(0)

    labels = page.locator(".ed-sheet-tools .ed-tool").all_inner_texts()
    assert "Negrita" in labels and "Subtítulo" in labels and "Quitar formato" in labels

    # The count is of what a reader sees, not of the HTML.
    count = page.locator("[data-ed-sheet-count]").inner_text()
    assert count.endswith("caracteres") and int(count.split()[0]) > 400


def test_the_page_editor_applies_and_cancels(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    base_url, password = admin_live_server
    restore("page.about.body")
    _open_editor(page, base_url, password, path="/nosotras")
    frame = page.frame_locator("[data-ed-iframe]")
    body = frame.locator('ct-t[data-k="page.about.body"]').first
    original = body.inner_text()

    body.click()
    page.locator("[data-ed-sheet-doc] p").first.click()
    page.keyboard.press("Home")
    page.keyboard.type("Hola. ")
    page.click("[data-ed-sheet-cancel]")
    expect(page.locator("[data-ed-sheet]")).to_be_hidden()
    expect(body).to_have_text(original)
    expect(page.locator("[data-ed-save]")).to_be_disabled()

    body.click()
    page.locator("[data-ed-sheet-doc] p").first.click()
    page.keyboard.press("Home")
    page.keyboard.type("Hola. ")
    page.click("[data-ed-sheet-apply]")
    expect(body).to_contain_text("Hola.")
    expect(page.locator("[data-ed-save]")).to_be_enabled()


def test_the_panel_sends_a_page_body_to_the_same_editor(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """A 756-character page in a 6-row textarea of raw HTML is how a stray </p>
    breaks the page."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password, path="/nosotras")
    page.click("[data-ed-panel-toggle]")
    page.click('[data-ed-tab="all"]')
    page.fill("[data-ed-filter]", "cuerpo")
    page.locator('[data-ed-field="page.about.body"] textarea').click()
    expect(page.locator("[data-ed-sheet]")).to_be_visible()


def test_focus_survives_a_save(
    admin_live_server: tuple[str, str], page: Page, restore
) -> None:
    """Save disables itself on success, which dropped focus to <body> at the moment a
    keyboard user most needs to know what happened."""
    base_url, password = admin_live_server
    restore("home.hero.eyebrow")
    _open_editor(page, base_url, password)
    _type_over(page, "home.hero.eyebrow", "Para guardar")

    page.locator("[data-ed-save]").focus()
    page.keyboard.press("Enter")
    expect(page.locator("[data-ed-status]")).to_contain_text("Borrador guardado")
    assert page.evaluate("() => document.activeElement.hasAttribute('data-ed-status')")


def test_the_skip_link_does_not_shove_the_canvas(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """As a static flex item it stretched to the full row height and pushed the site
    sideways while focused."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    before = page.locator("[data-ed-stage]").bounding_box()

    page.locator("[data-ed-skip]").focus()
    link = page.locator("[data-ed-skip]").bounding_box()
    after = page.locator("[data-ed-stage]").bounding_box()

    assert link and link["height"] < 70
    assert before and after and abs(after["x"] - before["x"]) < 2


def test_the_panel_tabs_behave_like_tabs(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """They announced themselves as a tablist and then ignored the arrow keys."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    page.click("[data-ed-panel-toggle]")

    tabs = page.locator("[data-ed-tab]")
    assert tabs.evaluate_all("ts => ts.map(t => t.tabIndex)").count(0) == 1
    assert all(tabs.evaluate_all("ts => ts.map(t => t.getAttribute('aria-controls'))"))

    page.locator('[data-ed-tab="hidden"]').focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator('[data-ed-tab="all"]')).to_have_attribute("aria-selected", "true")


# --- touch layout ---------------------------------------------------------------


@pytest.fixture
def phone(browser: Browser) -> Iterator[Page]:
    """A real touch context: a narrow desktop window hides most touch problems."""
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=2,
        reduced_motion="reduce",
    )
    pg = context.new_page()
    pg.set_default_timeout(DEFAULT_TIMEOUT_MS)
    pg.on("dialog", lambda dialog: dialog.accept())
    try:
        yield pg
    finally:
        context.close()


def test_the_canvas_fills_the_screen_on_a_phone(
    admin_live_server: tuple[str, str], phone: Page
) -> None:
    """`align-items: flex-start` on a COLUMN flex container sizes the width, so the
    canvas shrank to the iframe's intrinsic 300px on every touch device."""
    base_url, password = admin_live_server
    _open_editor(phone, base_url, password)
    inner = phone.frame_locator("[data-ed-iframe]").locator("body").evaluate(
        "() => window.innerWidth"
    )
    assert inner > 330, inner


def test_save_and_publish_stay_reachable_on_a_phone(
    admin_live_server: tuple[str, str], phone: Page
) -> None:
    """They used to scroll away — up to 1300px above the field being edited — and a
    `sticky` rule could not help because its containing block scrolls with the page."""
    base_url, password = admin_live_server
    _open_editor(phone, base_url, password)
    for offset in (0, 400, 1200):
        phone.evaluate(f"window.scrollTo(0, {offset})")
        box = phone.locator(".ed-actions").bounding_box()
        assert box and 0 <= box["y"] and box["y"] + box["height"] <= 844 + 2, (offset, box)


@pytest.mark.parametrize("width,height", [(390, 844), (844, 390), (320, 700)])
def test_the_editor_never_overflows_sideways_on_touch(
    admin_live_server: tuple[str, str], browser: Browser, width: int, height: int
) -> None:
    base_url, password = admin_live_server
    context = browser.new_context(
        viewport={"width": width, "height": height}, is_mobile=True, has_touch=True
    )
    try:
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        _open_editor(page, base_url, password)
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, f"{width}x{height} desborda {overflow}px"
    finally:
        context.close()


def test_the_device_switcher_is_hidden_where_it_cannot_help(
    admin_live_server: tuple[str, str], browser: Browser
) -> None:
    """A 1280px preview scaled into a phone renders 16px body text at 8px."""
    base_url, password = admin_live_server
    for width, height in ((390, 844), (844, 390)):
        context = browser.new_context(
            viewport={"width": width, "height": height}, is_mobile=True, has_touch=True
        )
        try:
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            _open_editor(page, base_url, password)
            expect(page.locator(".ed-devices")).to_be_hidden()
        finally:
            context.close()


def test_image_copy_is_advertised_without_covering_the_image(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """An <img> is a replaced element and generates no ::after, so the pencil badge
    never existed for exactly the case it was built for."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    frame = page.frame_locator("[data-ed-iframe]")
    # The hero image is covered by its own text overlay; the feature image is not.
    image = frame.locator(".feature-media img[data-ct-keys]").first

    outline = image.evaluate("el => getComputedStyle(el).outlineStyle")
    assert outline in ("dashed", "solid"), outline
    # …and it still opens its alt text.
    image.click()
    expect(page.locator("[data-ed-panel]")).to_be_visible()


def test_the_caret_lands_where_you_clicked_in_a_long_body(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """It used to snap to character 0, so a typo fix in paragraph four was typed into
    paragraph one."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password, path="/nosotras")
    frame = page.frame_locator("[data-ed-iframe]")
    body = frame.locator('ct-t[data-k="page.about.body"]').first

    box = body.bounding_box()
    assert box
    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.7)
    page.wait_for_timeout(200)

    # What matters is where the caret ends up ON SCREEN, not its character index:
    # it used to snap to the top of the whole body.
    caret_y = frame.locator("body").evaluate(
        "() => { const s = window.getSelection(); if (!s || !s.rangeCount) return -1;"
        " const r = s.getRangeAt(0).cloneRange(); const probe = document.createElement('span');"
        " probe.textContent = '\u200b'; r.insertNode(probe);"
        " const top = probe.getBoundingClientRect().top; probe.remove(); return top; }"
    )
    assert caret_y > 0, "no se pudo medir el cursor"
    # 70% down a 362px block: landing anywhere in the lower half is the point.
    assert caret_y > box["height"] * 0.35, (
        f"el cursor quedó a {caret_y:.0f}px del tope del bloque de {box['height']:.0f}px"
    )


# --- the admin chrome -----------------------------------------------------------


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (390, 844), (320, 700), (844, 390)])
def test_the_admin_header_is_one_row_at_every_width(
    admin_live_server: tuple[str, str], browser: Browser, width: int, height: int
) -> None:
    """It was a flex row that wrapped to three lines on a small phone and pushed 129px
    of horizontal overflow at narrow widths."""
    base_url, password = admin_live_server
    context = browser.new_context(viewport={"width": width, "height": height})
    try:
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        _open_editor(page, base_url, password)

        header = page.locator(".adm-header").bounding_box()
        assert header and header["height"] <= 72, header
        assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0

        # Every control in the bar clears the touch floor.
        for selector in (".adm-section-link", ".adm-exit"):
            box = page.locator(selector).first.bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44, (selector, box)
    finally:
        context.close()


def test_the_current_section_is_not_marked_by_colour_alone(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    current = page.locator(".adm-section-link.is-current")
    expect(current).to_have_attribute("aria-current", "page")
    assert current.evaluate("el => getComputedStyle(el).fontWeight") in ("500", "600", "700")


def test_the_panel_close_button_is_not_buried_under_the_toolbar(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """The panel was pinned at a hardcoded offset while the toolbar sat above it in
    the stacking order, so its × could not be clicked."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password)
    page.click("[data-ed-panel-toggle]")
    page.evaluate("window.scrollTo(0, 400)")

    close = page.locator("[data-ed-panel-close]")
    box = close.bounding_box()
    assert box
    on_top = page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).hasAttribute('data-ed-panel-close')",
        [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
    )
    assert on_top, "algo tapa el botón de cerrar"
    close.click()
    expect(page.locator("[data-ed-panel]")).to_be_hidden()


def test_the_page_editor_behaves_like_a_dialog(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """It declares role="dialog" aria-modal="true", so focus has to stay inside: a
    keyboard user must not tab out into a page they cannot see behind the overlay."""
    base_url, password = admin_live_server
    _open_editor(page, base_url, password, path="/nosotras")
    frame = page.frame_locator("[data-ed-iframe]")

    opener = page.locator("[data-ed-panel-toggle]")
    opener.focus()
    frame.locator('ct-t[data-k="page.about.body"]').first.click()
    expect(page.locator("[data-ed-sheet]")).to_be_visible()

    inside = lambda: page.evaluate(
        "() => document.querySelector('[data-ed-sheet]').contains(document.activeElement)"
    )
    assert inside(), "el foco no entró al diálogo"

    # Tab all the way round: it must never leave.
    for _ in range(25):
        page.keyboard.press("Tab")
        assert inside(), "el foco se escapó del diálogo"
    for _ in range(6):
        page.keyboard.press("Shift+Tab")
        assert inside(), "el foco se escapó del diálogo hacia atrás"

    page.keyboard.press("Escape")
    expect(page.locator("[data-ed-sheet]")).to_be_hidden()
    # Opened from the canvas, so focus goes back to the canvas — not to <body>.
    assert page.evaluate("() => document.activeElement.tagName") == "IFRAME"


def test_the_page_editor_returns_focus_to_the_panel_field_it_came_from(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _open_editor(page, base_url, password, path="/nosotras")

    page.click("[data-ed-panel-toggle]")
    page.click('[data-ed-tab="all"]')
    page.fill("[data-ed-filter]", "cuerpo")
    field = page.locator('[data-ed-field="page.about.body"] textarea')
    field.click()
    expect(page.locator("[data-ed-sheet]")).to_be_visible()

    # Closing returns focus to the field — which must NOT reopen the sheet, or the
    # two would ping-pong forever.
    page.keyboard.press("Escape")
    expect(page.locator("[data-ed-sheet]")).to_be_hidden()
    assert page.evaluate(
        "() => document.activeElement.closest('[data-ed-field]')?.dataset.edField"
    ) == "page.about.body"

    # …and Enter on it opens the sheet again, for keyboard users.
    page.keyboard.press("Enter")
    expect(page.locator("[data-ed-sheet]")).to_be_visible()
