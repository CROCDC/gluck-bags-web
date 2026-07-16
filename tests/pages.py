"""Single source of truth for the responsive / overflow layers.

The responsive test (``test_responsive.py``) and the overflow guard
(``test_overflow_detection.py``) both key off these lists, so they can never
drift: the same pages, the same viewport matrix, the same structural containers.

Import note: this assumes pytest's DEFAULT import mode with ``tests/`` NOT a
package (no ``__init__.py``) — sibling modules import by bare name
(``from pages import ...``).
"""

from __future__ import annotations

# Public (no-auth) pages served as full HTML documents. The product detail page
# (``/producto/<id>``) is intentionally absent: its id depends on the seeded data,
# so it's discovered at runtime (see ``_discover_product_path``) rather than
# hardcoded here.
PUBLIC_PAGES: list[str] = ["/"]

# Phone (small + large), tablet portrait, small laptop, desktop. Screenshots are
# named ``<label>-<width>.png`` off these widths, so the committed baseline keys
# off the numbers — don't rename them without re-recording the baseline.
VIEWPORT_WIDTHS: list[int] = [375, 414, 768, 1024, 1280]
VIEWPORT_HEIGHT: int = 900

# Allow one CSS pixel of slop for sub-pixel rounding in getBoundingClientRect().
OVERFLOW_TOLERANCE: float = 1.0


def page_slug(path: str) -> str:
    """Filesystem-safe screenshot label for a path ('/' -> 'home')."""
    return path.strip("/").replace("/", "-") or "home"


# --- structural containers ----------------------------------------------------
# The wrappers that define each page's width. We probe these (not every leaf) so a
# decorative full-bleed element doesn't trip the overflow guard, but a layout
# container that exceeds the viewport does. The overflow guard additionally
# asserts these selectors actually resolve, so a renamed class can't silently
# disable the check (see test_overflow_detection.py).

PUBLIC_CONTAINERS: list[str] = [
    "body",
    "header.site-header",
    ".header-inner",
    "main",
    "footer.site-footer",
    ".footer-top",
    ".footer-bottom",
]

# Extra containers specific to the home page sections.
HOME_CONTAINERS: list[str] = PUBLIC_CONTAINERS + [
    ".cat-grid",
    ".shop-grid",
    ".feature",
    ".manifiesto-inner",
]

# Extra containers specific to the product detail page. (.pdp-thumbs is NOT
# listed: it only renders for multi-image products, and the resolve-guard test
# requires every selector to exist on the seeded single-image PDP.)
PDP_CONTAINERS: list[str] = PUBLIC_CONTAINERS + [
    ".pdp",
    ".pdp-grid",
    ".pdp-media-col",
    ".pdp-gallery",
    ".pdp-info",
]

ADMIN_CONTAINERS: list[str] = [
    "body.admin",
    "header.adm-header",
    "main.adm-main",
]

ADMIN_LOGIN_CONTAINERS: list[str] = ADMIN_CONTAINERS + [".adm-login", ".adm-form"]
ADMIN_LIST_CONTAINERS: list[str] = ADMIN_CONTAINERS + [".adm-list-head", ".adm-list"]
ADMIN_FORM_CONTAINERS: list[str] = ADMIN_CONTAINERS + [
    "#productForm",
    ".adm-field",
    ".uploader",
    ".adm-form-actions",
]

# Containers expected to be present on each PUBLIC page — the overflow guard uses
# this to verify the selectors still match real elements. Admin pages live behind
# auth, so they aren't part of the public guard; the responsive admin tests still
# probe ADMIN_*_CONTAINERS directly.
PUBLIC_PAGE_CONTAINERS: dict[str, list[str]] = {
    "/": HOME_CONTAINERS,
}
