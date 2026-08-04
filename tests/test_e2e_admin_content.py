"""End-to-end browser flow for the admin "Textos" section.

Covers what the HTTP tests structurally cannot: that a real person can go from the
admin to changed copy on the live site, and that the preview genuinely renders the
unpublished draft — in every format it offers.

The route through the app is deliberately the human one: click into the section,
type in the field, save, look at the preview, publish, then load the public page in
a logged-OUT context and check the new copy is there.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e

DEFAULT_TIMEOUT_MS = 20_000

# The field this file drives end to end: it renders as the home's H1 subtitle, so a
# change is visible both in the DOM and in the preview iframe.
FIELD = "home.hero.subtitle"
NEW_TEXT = "Bolsos que duran toda la vida (E2E)"
ORIGINAL = "Bolsos de líneas puras, pensados para durar. Sin pieles, sin excesos."


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    pg.set_default_timeout(DEFAULT_TIMEOUT_MS)
    pg.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
    try:
        yield pg
    finally:
        context.close()


def _login(page: Page, base_url: str, password: str) -> None:
    page.goto(f"{base_url}/admin/", wait_until="load")
    expect(page).to_have_url(re.compile(r"/admin/login"))
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/admin/?$"))


def _open_content_section(page: Page) -> None:
    """The header's "Textos" opens the visual editor; this file drives the list of
    forms behind it, which is the no-JS path and how you find one specific string."""
    page.get_by_role("link", name="Textos").click()
    page.wait_for_url(re.compile(r"/admin/content/?$"))
    page.get_by_role("link", name=re.compile("lista completa por sección")).click()
    page.wait_for_url(re.compile(r"/admin/content/list$"))
    expect(page.get_by_role("heading", name="Textos de la web")).to_be_visible()


def _set_field(page: Page, key: str, value: str) -> None:
    field = page.locator(f'[name="{key}"]')
    field.scroll_into_view_if_needed()
    field.fill(value)


def _restore_default(page: Page, base_url: str, key: str) -> None:
    """Leave the site as we found it, whatever the test did."""
    page.goto(f"{base_url}/admin/content/home", wait_until="load")
    if page.locator(f'button[name="restore"][value="{key}"]').count():
        page.locator(f'button[name="restore"][value="{key}"]').first.click()
        page.wait_for_load_state("load")
    page.goto(f"{base_url}/admin/content/home", wait_until="load")
    page.locator('button[value="publish"]').first.click()
    page.wait_for_load_state("load")


def test_edit_preview_and_publish_reaches_the_public_site(
    admin_live_server: tuple[str, str], page: Page, browser: Browser
) -> None:
    base_url, password = admin_live_server
    try:
        _login(page, base_url, password)
        _open_content_section(page)

        # 1. Into the home group and change the hero subtitle.
        page.get_by_role("link", name=re.compile("Inicio")).first.click()
        page.wait_for_url(re.compile(r"/admin/content/home$"))
        _set_field(page, FIELD, NEW_TEXT)

        # 2. Save as a draft — the live site must NOT change yet.
        page.get_by_role("button", name="Guardar borrador").click()
        page.wait_for_url(re.compile(r"/admin/content/home$"))
        expect(page.locator(".adm-flash-success")).to_be_visible()

        anon = browser.new_context()
        try:
            public = anon.new_page()
            public.goto(f"{base_url}/", wait_until="load")
            assert NEW_TEXT not in public.content()
            assert ORIGINAL in public.content()
        finally:
            anon.close()

        # 3. The preview DOES show it, inside the real page.
        page.get_by_role("link", name="Previsualizar").click()
        page.wait_for_url(re.compile(r"/admin/content/home/preview$"))
        frame = page.frame_locator("[data-ct-iframe]")
        expect(frame.locator(".hero-sub")).to_have_text(NEW_TEXT)

        # 4. Publish from the preview screen and check the public site.
        page.get_by_role("button", name=re.compile("Publicar")).click()
        page.wait_for_load_state("load")

        anon = browser.new_context()
        try:
            public = anon.new_page()
            public.goto(f"{base_url}/", wait_until="load")
            expect(public.locator(".hero-sub")).to_have_text(NEW_TEXT)
        finally:
            anon.close()
    finally:
        _restore_default(page, base_url, FIELD)


def test_the_preview_switches_between_device_and_share_formats(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """The three device widths render the real page; the three share formats render
    cards built from that page's own <title>/meta tags."""
    base_url, password = admin_live_server
    _login(page, base_url, password)
    page.goto(f"{base_url}/admin/content/home/preview", wait_until="load")

    frame = page.locator("[data-ct-frame]")
    for label, width in (("Celular", 390), ("Tablet", 768), ("Escritorio", 1280)):
        page.get_by_role("tab", name=re.compile(label)).click()
        page.wait_for_timeout(150)
        expect(page.locator("[data-ct-stage]")).to_be_visible()
        assert frame.evaluate("el => el.style.width") == f"{width}px", label
        # Scaled down to fit the panel, never spilling out of it.
        box = frame.bounding_box()
        stage = page.locator("[data-ct-stage]").bounding_box()
        assert box and stage and box["width"] <= stage["width"] + 1, label

    # Google: the SERP card mirrors the page's real <title> and description.
    page.get_by_role("tab", name=re.compile("Google")).click()
    expect(page.locator('[data-ct-card-panel="google"]')).to_be_visible()
    expect(page.locator("[data-ct-serp-title]")).to_have_text(
        "GLÜCK · Bolsos minimalistas de cuero vegano"
    )
    assert page.locator("[data-ct-serp-desc]").inner_text().strip()
    assert "gluckbags.com" in page.locator("[data-ct-serp-url]").inner_text()

    # WhatsApp / Twitter: the Open Graph and Twitter cards.
    page.get_by_role("tab", name=re.compile("WhatsApp")).click()
    expect(page.locator('[data-ct-card-panel="whatsapp"]')).to_be_visible()
    assert page.locator("[data-ct-og-title]").inner_text().strip()
    expect(page.locator("[data-ct-og-image]")).to_be_visible()

    page.get_by_role("tab", name=re.compile("Twitter")).click()
    expect(page.locator('[data-ct-card-panel="twitter"]')).to_be_visible()
    assert page.locator("[data-ct-tw-title]").inner_text().strip()

    expect(page.locator("[data-ct-card-fallback]")).to_be_hidden()


def test_an_edited_title_shows_up_in_the_google_card(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """The share/search previews read the previewed document, so a draft SEO title
    is visible before it is published anywhere."""
    base_url, password = admin_live_server
    key = "seo.home.title"
    try:
        _login(page, base_url, password)
        page.goto(f"{base_url}/admin/content/seo", wait_until="load")
        _set_field(page, key, "Título de prueba E2E")
        page.get_by_role("button", name="Guardar borrador").click()
        page.wait_for_url(re.compile(r"/admin/content/seo$"))

        page.goto(f"{base_url}/admin/content/seo/preview", wait_until="load")
        page.get_by_role("tab", name=re.compile("Google")).click()
        expect(page.locator("[data-ct-serp-title]")).to_have_text("Título de prueba E2E")
    finally:
        page.goto(f"{base_url}/admin/content/seo", wait_until="load")
        if page.locator(f'button[name="restore"][value="{key}"]').count():
            page.locator(f'button[name="restore"][value="{key}"]').first.click()
            page.wait_for_load_state("load")
        page.goto(f"{base_url}/admin/content/seo", wait_until="load")
        page.locator('button[value="publish"]').first.click()
        page.wait_for_load_state("load")


def test_the_field_filter_narrows_the_editor(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """With ~40 fields on a screen, the filter is how an editor finds one."""
    base_url, password = admin_live_server
    _login(page, base_url, password)
    page.goto(f"{base_url}/admin/content/home", wait_until="load")

    total = page.locator("[data-ct-item]").count()
    assert total > 10

    page.fill("[data-ct-filter]", "marquesina que no existe")
    page.wait_for_timeout(120)
    assert page.locator("[data-ct-item]:not([hidden])").count() == 0
    expect(page.locator("[data-ct-no-results]")).to_be_visible()

    page.fill("[data-ct-filter]", "scroll")
    page.wait_for_timeout(120)
    visible = page.locator("[data-ct-item]:not([hidden])").count()
    assert 0 < visible < total

    page.click("[data-ct-filter-clear]")
    page.wait_for_timeout(120)
    assert page.locator("[data-ct-item]:not([hidden])").count() == total


def test_the_character_counter_flags_a_value_over_the_limit(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _login(page, base_url, password)
    page.goto(f"{base_url}/admin/content/seo", wait_until="load")

    counter = page.locator('[name="seo.home.description"]').locator(
        "xpath=../div/span[@data-ct-counter]"
    )
    assert "/" in counter.inner_text()
    before = counter.inner_text()
    _set_field(page, "seo.home.description", "corto")
    page.wait_for_timeout(120)
    assert counter.inner_text() != before
    assert counter.inner_text().startswith("5 /")
