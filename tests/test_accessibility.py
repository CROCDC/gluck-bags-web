"""Accessibility smoke across the site (Playwright, deterministic & offline).

This is a structural a11y guard, NOT an axe/CDN audit: every assertion is a
hand-rolled DOM check run in the page via `evaluate`, so it works fully offline
and only fails on a real, observable regression. We cover the five content
pages a visitor/operator actually lands on — the public home, the first
/producto/<id> detail (its href discovered from the home grid), the admin login,
the admin product list, and the admin "new product" form — and assert, per page,
the relevant subset of:

  * <html lang="es">,
  * exactly ONE <h1>,
  * every <img> carries an `alt` attribute (empty is fine for decorative),
  * every native, non-hidden form control has a programmatic label
    (<label for>, wrapping <label>, or aria-label/aria-labelledby),
  * every <button>/<a> has an accessible name (text, aria-label or title),
  * the landmark set the layout promises (public: header/nav/main/footer;
    admin: header.adm-header + main.adm-main),
  * a non-empty <title>.

Where the real templates fall short of an idealised rule we assert the GENUINE
state instead of inventing a requirement: the admin login's password field has
no <label>/aria-label (it relies on a visible placeholder + autocomplete), so we
assert that real, weaker state and skip the strict label rule only for that one
known control — see test_admin_login_password_field_a11y_state.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page

pytestmark = pytest.mark.e2e

DEFAULT_TIMEOUT_MS = 30_000


# --- page fixture (fresh, isolated context per test) -------------------------


@pytest.fixture
def page(browser: Browser):
    context = browser.new_context()
    pg = context.new_page()
    pg.set_default_timeout(DEFAULT_TIMEOUT_MS)
    pg.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
    try:
        yield pg
    finally:
        context.close()


# --- reusable in-page a11y probes --------------------------------------------

# Returns the structural a11y facts we assert on, in one round-trip so a single
# evaluate covers most rules. `controlIssues` lists native form controls that
# lack ANY programmatic label; `nameIssues` lists buttons/links with no
# accessible name. Both carry a short identifier so failures are actionable.
_AUDIT_JS = r"""
() => {
  const lang = document.documentElement.getAttribute('lang');
  const title = (document.title || '').trim();
  const h1Count = document.querySelectorAll('h1').length;

  // Every <img> must have the alt attribute present (value may be '').
  const imgsAllHaveAlt = Array.from(document.images).every(i => i.hasAttribute('alt'));

  const describe = (el) => {
    const id = el.id ? '#' + el.id : '';
    const name = el.getAttribute('name');
    const type = el.getAttribute('type');
    return (el.tagName.toLowerCase()
            + (type ? '[type=' + type + ']' : '')
            + id + (name ? '(name=' + name + ')' : ''));
  };

  const hasLabel = (el) => {
    // aria-label / aria-labelledby win outright.
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return true;
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby && labelledby.trim()) {
      return labelledby.split(/\s+/).some(rid => {
        const t = document.getElementById(rid);
        return t && t.textContent.trim();
      });
    }
    // An explicit <label for=id> whose text is non-empty.
    if (el.id) {
      const forLabel = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (forLabel && forLabel.textContent.trim()) return true;
    }
    // A wrapping <label> with text.
    const wrap = el.closest('label');
    if (wrap && wrap.textContent.trim()) return true;
    // title is a (weak) accessible-name source the AT will read.
    const title = el.getAttribute('title');
    if (title && title.trim()) return true;
    return false;
  };

  const controls = Array.from(document.querySelectorAll('input, select, textarea'))
    .filter(el => (el.getAttribute('type') || '').toLowerCase() !== 'hidden');
  const controlIssues = controls.filter(el => !hasLabel(el)).map(describe);

  // Buttons and links need an accessible name: visible text, aria-label or title.
  const accName = (el) => {
    const text = (el.textContent || '').trim();
    if (text) return true;
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return true;
    const title = el.getAttribute('title');
    if (title && title.trim()) return true;
    // An <img alt> / <svg> inside can also name it; count a non-empty img alt.
    const img = el.querySelector('img[alt]');
    if (img && (img.getAttribute('alt') || '').trim()) return true;
    return false;
  };
  const interactive = Array.from(document.querySelectorAll('button, a'));
  const nameIssues = interactive.filter(el => !accName(el)).map(describe);

  return { lang, title, h1Count, imgsAllHaveAlt, controlIssues, nameIssues };
}
"""


def _audit(page: Page) -> dict:
    return page.evaluate(_AUDIT_JS)


def _landmarks(page: Page) -> dict:
    """Count the structural landmark elements a page's layout promises."""
    return page.evaluate(
        """() => ({
            header: document.querySelectorAll('header').length,
            nav: document.querySelectorAll('nav').length,
            main: document.querySelectorAll('main').length,
            footer: document.querySelectorAll('footer').length,
            admHeader: document.querySelectorAll('header.adm-header').length,
            admMain: document.querySelectorAll('main.adm-main').length,
        })"""
    )


# --- navigation helpers ------------------------------------------------------


def _goto(page: Page, base_url: str, path: str) -> None:
    page.goto(base_url.rstrip("/") + path, wait_until="load")


def _discover_product_path(page: Page, base_url: str) -> str:
    """Return the first /producto/<id> path linked from the home grid."""
    _goto(page, base_url, "/")
    href = page.evaluate(
        "() => { const a = document.querySelector('a[href*=\"/producto/\"]');"
        " return a ? a.getAttribute('href') : null; }"
    )
    assert href, "No /producto/<id> link on the home page (seeded products expected)."
    href = re.sub(r"^https?://[^/]+", "", href)
    return href.split("#")[0]


def _login(page: Page, base_url: str, password: str) -> None:
    _goto(page, base_url, "/admin/login")
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/admin/?$"))


# --- shared cross-page invariants --------------------------------------------


def _assert_common(audit: dict, *, expect_one_h1: bool = True) -> None:
    """Invariants every content page must satisfy."""
    assert audit["lang"] == "es-AR", f"<html lang> is {audit['lang']!r}, expected 'es-AR'"
    assert audit["title"], "document <title> is empty"
    assert audit["imgsAllHaveAlt"], "some <img> lacks an alt attribute"
    assert not audit["nameIssues"], (
        f"buttons/links without an accessible name: {audit['nameIssues']}"
    )
    if expect_one_h1:
        assert audit["h1Count"] == 1, f"expected exactly one <h1>, found {audit['h1Count']}"


# --- public pages ------------------------------------------------------------


def test_home_accessibility(admin_live_server: tuple[str, str], page: Page) -> None:
    base_url, _ = admin_live_server
    _goto(page, base_url, "/")
    audit = _audit(page)
    _assert_common(audit)
    # Home has no native form controls; nothing to label, so the list is empty.
    assert not audit["controlIssues"], (
        f"unlabeled form controls on home: {audit['controlIssues']}"
    )

    lm = _landmarks(page)
    assert lm["header"] >= 1, "public home is missing a <header> landmark"
    assert lm["nav"] >= 1, "public home is missing a <nav> landmark"
    assert lm["main"] == 1, f"public home should have exactly one <main>, has {lm['main']}"
    assert lm["footer"] >= 1, "public home is missing a <footer> landmark"


def test_product_detail_accessibility(admin_live_server: tuple[str, str], page: Page) -> None:
    base_url, _ = admin_live_server
    product_path = _discover_product_path(page, base_url)
    _goto(page, base_url, product_path)
    audit = _audit(page)
    _assert_common(audit)
    assert not audit["controlIssues"], (
        f"unlabeled form controls on product detail: {audit['controlIssues']}"
    )

    lm = _landmarks(page)
    assert lm["header"] >= 1, "product detail is missing a <header> landmark"
    assert lm["nav"] >= 1, "product detail is missing a <nav> landmark"
    assert lm["main"] == 1, f"product detail should have one <main>, has {lm['main']}"
    assert lm["footer"] >= 1, "product detail is missing a <footer> landmark"


# --- admin pages -------------------------------------------------------------


def test_admin_login_accessibility(admin_live_server: tuple[str, str], page: Page) -> None:
    base_url, _ = admin_live_server
    _goto(page, base_url, "/admin/login")
    audit = _audit(page)
    # lang/title/one-h1/imgs/accessible-names hold even though the password field
    # has no <label> (asserted separately, below).
    assert audit["lang"] == "es"
    assert audit["title"], "admin login <title> is empty"
    assert audit["h1Count"] == 1, f"admin login should have one <h1>, has {audit['h1Count']}"
    assert audit["imgsAllHaveAlt"], "an <img> on admin login lacks alt"
    assert not audit["nameIssues"], (
        f"admin login buttons/links without accessible name: {audit['nameIssues']}"
    )

    lm = _landmarks(page)
    assert lm["admHeader"] == 1, "admin login is missing header.adm-header"
    assert lm["admMain"] == 1, "admin login is missing main.adm-main"


def test_admin_login_password_field_a11y_state(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """The login password field must be named programmatically, not by a placeholder.

    This test used to document the opposite — that the field relied on its
    placeholder — and told whoever fixed it to tighten the rule. That happened, so
    the rule is now the strict one.
    """
    base_url, _ = admin_live_server
    _goto(page, base_url, "/admin/login")
    state = page.evaluate(
        """() => {
            const el = document.querySelector('input[name=\"password\"]');
            if (!el) return null;
            return {
                type: el.getAttribute('type'),
                hasForLabel: !!(el.id && document.querySelector('label[for=\"' + el.id + '\"]')),
                wrapped: !!el.closest('label'),
                ariaLabel: el.getAttribute('aria-label'),
                placeholder: el.getAttribute('placeholder'),
                autocomplete: el.getAttribute('autocomplete'),
                required: el.hasAttribute('required'),
            };
        }"""
    )
    assert state is not None, "password input not found on admin login"
    assert state["type"] == "password"
    assert state["required"], "password input should be required"
    assert state["autocomplete"] == "current-password"
    # A placeholder is not an accessible name: it disappears the moment you type.
    assert state["hasForLabel"] or state["wrapped"] or state["ariaLabel"], (
        "el campo de contraseña sólo está nombrado por su placeholder"
    )


def test_admin_list_accessibility(admin_live_server: tuple[str, str], page: Page) -> None:
    base_url, password = admin_live_server
    _login(page, base_url, password)
    audit = _audit(page)
    assert audit["lang"] == "es"
    assert audit["title"], "admin list <title> is empty"
    assert audit["h1Count"] == 1, f"admin list should have one <h1>, has {audit['h1Count']}"
    assert audit["imgsAllHaveAlt"], "an <img> on admin list lacks alt"
    assert not audit["nameIssues"], (
        f"admin list has unnamed buttons/links: {audit['nameIssues']}"
    )
    # The list has no labelable native controls (only submit buttons).
    assert not audit["controlIssues"], (
        f"unexpected unlabeled controls on admin list: {audit['controlIssues']}"
    )

    # Pinpoint the icon-only toggle/delete buttons. Their visible content is a bare
    # emoji glyph (🙈/👁/🗑), which the generic nameIssues probe accepts as text —
    # so it would NOT notice a regression that strips the *meaningful* name. A lone
    # emoji is announced literally ("wastebasket"), not "Borrar", so the real
    # accessible name has to come from aria-label/title. Assert that here so losing
    # those attributes fails loudly, and check the names are the human-readable ones.
    iconbtns = page.evaluate(
        """() => Array.from(document.querySelectorAll('button.adm-iconbtn')).map(b => ({
            ariaLabel: (b.getAttribute('aria-label') || '').trim(),
            title: (b.getAttribute('title') || '').trim(),
            text: (b.textContent || '').trim(),
        }))"""
    )
    assert iconbtns, "no .adm-iconbtn buttons on the admin list (seeded products expected)"
    EXPECTED_NAMES = {"Ocultar de la web", "Mostrar en la web", "Borrar"}
    for b in iconbtns:
        name = b["ariaLabel"] or b["title"]
        assert name, (
            f"icon-only button has no aria-label/title (emoji {b['text']!r} is not a "
            f"real accessible name): {b}"
        )
        assert name in EXPECTED_NAMES, f"unexpected icon-button name {name!r}"
    # Both behaviours must be present: a delete and a hide/show toggle.
    names = {b["ariaLabel"] or b["title"] for b in iconbtns}
    assert "Borrar" in names, "admin list is missing a labeled delete button"
    assert names & {"Ocultar de la web", "Mostrar en la web"}, (
        "admin list is missing a labeled visibility-toggle button"
    )

    lm = _landmarks(page)
    assert lm["admHeader"] == 1, "admin list is missing header.adm-header"
    assert lm["admMain"] == 1, "admin list is missing main.adm-main"


def test_admin_new_product_form_accessibility(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    base_url, password = admin_live_server
    _login(page, base_url, password)
    _goto(page, base_url, "/admin/products/new")
    page.wait_for_selector("#productForm")
    audit = _audit(page)
    assert audit["lang"] == "es"
    assert audit["title"], "admin form <title> is empty"
    assert audit["h1Count"] == 1, f"admin form should have one <h1>, has {audit['h1Count']}"
    assert audit["imgsAllHaveAlt"], "an <img> on the admin form lacks alt"
    assert not audit["nameIssues"], (
        f"admin form buttons/links without accessible name: {audit['nameIssues']}"
    )
    # The headline a11y assertion for this page: EVERY visible form control on the
    # product form (title/price/category/description + the is_published checkbox +
    # the file input) is programmatically labeled.
    assert not audit["controlIssues"], (
        f"product form has unlabeled controls: {audit['controlIssues']}"
    )

    lm = _landmarks(page)
    assert lm["admHeader"] == 1, "admin form is missing header.adm-header"
    assert lm["admMain"] == 1, "admin form is missing main.adm-main"


def test_admin_new_product_form_each_field_labeled(
    admin_live_server: tuple[str, str], page: Page
) -> None:
    """Pinpoint check: each named product-form field resolves to a real
    <label for=id>, so a regression naming a single field is caught precisely."""
    base_url, password = admin_live_server
    _login(page, base_url, password)
    _goto(page, base_url, "/admin/products/new")
    page.wait_for_selector("#productForm")
    labeled = page.evaluate(
        """(ids) => {
            const out = {};
            for (const id of ids) {
                const el = document.getElementById(id);
                const lbl = el ? document.querySelector('label[for=\"' + id + '\"]') : null;
                out[id] = !!(el && lbl && lbl.textContent.trim());
            }
            return out;
        }""",
        ["title", "price", "category", "description", "is_published", "fileInput"],
    )
    missing = [field for field, ok in labeled.items() if not ok]
    assert not missing, f"product-form fields without a <label for=id>: {missing}"
