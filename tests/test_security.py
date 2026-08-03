"""Security-focused tests for the GLÜCK admin + public site.

Dimension: security. Covers, end to end against the Flask test client:

* Authorization: every admin *mutation* endpoint (the list, new/edit GET+POST,
  delete, toggle, reorder) must bounce an anonymous caller to the login page and
  must NOT perform the action (no DB writes, no media touched).
* Path traversal on the public `/media/<path>` route: encoded/relative `..`
  escapes must never serve files outside MEDIA_ROOT (the SQLite DB, /etc/passwd,
  the app source, …). They must return a non-200 (404/403) and never leak bytes.
* Stored XSS: a product whose title/description contains a `<script>` payload is
  HTML-escaped on both the home grid and the product detail page — the literal
  tag text appears escaped, never as a live, executable tag.
* Admin pages carry `<meta name="robots" content="noindex, nofollow">` so the
  panel is never indexed.
* MAX_CONTENT_LENGTH is configured to a positive cap (DoS / huge-upload guard).
* A wrong (or empty) login never creates an authenticated session.
* CSRF: the session cookie is SameSite=Lax + HttpOnly (and Secure in production),
  so a forged cross-site POST can't carry the admin's cookie. The tests below
  assert that mitigation is in place.

All tests rely only on the shared conftest fixtures (`client`, `auth_client`,
`app`) and are hermetic / order-independent.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from flask import Flask
from flask.testing import FlaskClient

# --- URL constants (a route rename surfaces here in one place) ----------------

LOGIN_URL = "/admin/login"
LIST_URL = "/admin/"
NEW_URL = "/admin/products/new"
REORDER_URL = "/admin/products/reorder"


def _read_sample_image_bytes() -> bytes:
    """A real, valid JPEG shipped with the app (used as a benign upload)."""
    here = Path(__file__).resolve().parent.parent
    path = here / "app" / "static" / "img" / "productos" / "crossbody-rosa.jpg"
    return path.read_bytes()


def _login(client: FlaskClient, password: str) -> None:
    client.post(LOGIN_URL, data={"password": password})


def _create_product(app: Flask, **kwargs: Any) -> int:
    """Create a product directly via the repository; return its id.

    Used to seed state for authorization tests without going through the UI.
    """
    with app.app_context():
        from app.repositories import ProductRepository

        product = ProductRepository.create(
            title=kwargs.get("title", "Sample"),
            description=kwargs.get("description", ""),
            price=kwargs.get("price"),
            category=kwargs.get("category"),
            is_published=kwargs.get("is_published", True),
        )
        return product.id


def _product_count(app: Flask) -> int:
    with app.app_context():
        from app.models import Product

        return Product.query.count()


def _get_product(app: Flask, product_id: int) -> Any:
    with app.app_context():
        from app.repositories import ProductRepository

        return ProductRepository.get_by_id(product_id)


# --- Authorization: anonymous callers cannot reach admin mutations ------------
# Every login-protected view must redirect (302) to the login page. We assert
# both the redirect AND that the underlying action did NOT happen.


def test_admin_list_redirects_anonymous(client: FlaskClient) -> None:
    r = client.get(LIST_URL)
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")


def test_admin_new_get_redirects_anonymous(client: FlaskClient) -> None:
    r = client.get(NEW_URL)
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")


def test_admin_new_post_redirects_and_creates_nothing(
    client: FlaskClient, app: Flask
) -> None:
    before = _product_count(app)
    r = client.post(
        NEW_URL,
        data={"title": "Injected by anonymous", "is_published": "on"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")
    # The product must NOT have been created — the guard runs before the action.
    assert _product_count(app) == before


def test_admin_edit_get_redirects_anonymous(client: FlaskClient, app: Flask) -> None:
    pid = _create_product(app, title="Original")
    r = client.get(f"/admin/products/{pid}/edit")
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")


def test_admin_edit_post_redirects_and_does_not_mutate(
    client: FlaskClient, app: Flask
) -> None:
    pid = _create_product(app, title="Original", is_published=True)
    r = client.post(
        f"/admin/products/{pid}/edit",
        data={"title": "Hacked title", "is_published": "on"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")
    # Title must be untouched.
    assert _get_product(app, pid).title == "Original"


def test_admin_delete_redirects_and_does_not_delete(
    client: FlaskClient, app: Flask
) -> None:
    pid = _create_product(app, title="Keep me")
    r = client.post(f"/admin/products/{pid}/delete")
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")
    # Still present.
    assert _get_product(app, pid) is not None


def test_admin_toggle_redirects_and_does_not_toggle(
    client: FlaskClient, app: Flask
) -> None:
    pid = _create_product(app, title="Pub", is_published=True)
    r = client.post(f"/admin/products/{pid}/toggle")
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")
    # Publish flag unchanged.
    assert _get_product(app, pid).is_published is True


def test_admin_reorder_redirects_and_does_not_reorder(
    client: FlaskClient, app: Flask
) -> None:
    a = _create_product(app, title="A")
    b = _create_product(app, title="B")
    r = client.post(REORDER_URL, json={"order": [b, a]})
    # A JSON POST gets JSON back, not a redirect to the login PAGE: this endpoint is
    # only ever called from fetch(), and following that redirect made an expired
    # session surface as an HTML-parsed-as-JSON error (see app/auth.py).
    assert r.status_code == 401
    assert r.get_json()["reason"] == "auth"
    # Positions must be untouched (created in order -> a before b).
    with app.app_context():
        from app.repositories import ProductRepository

        prod_a = ProductRepository.get_by_id(a)
        prod_b = ProductRepository.get_by_id(b)
        assert prod_a.position < prod_b.position


def test_every_admin_mutation_endpoint_is_guarded(client: FlaskClient, app: Flask) -> None:
    """Sweep: no admin mutation endpoint is reachable while anonymous."""
    pid = _create_product(app, title="Guard sweep")
    cases: list[tuple[str, str]] = [
        ("GET", LIST_URL),
        ("GET", NEW_URL),
        ("POST", NEW_URL),
        ("GET", f"/admin/products/{pid}/edit"),
        ("POST", f"/admin/products/{pid}/edit"),
        ("POST", f"/admin/products/{pid}/delete"),
        ("POST", f"/admin/products/{pid}/toggle"),
        ("POST", REORDER_URL),
    ]
    for method, url in cases:
        r = client.open(url, method=method)
        assert r.status_code == 302, f"{method} {url} should redirect anonymous"
        assert LOGIN_URL in r.headers.get("Location", ""), f"{method} {url} -> login"


# --- Path traversal on /media -------------------------------------------------
# send_from_directory must confine reads to MEDIA_ROOT. We try a variety of
# encoded and raw traversal payloads aimed at files we KNOW exist outside the
# media folder, and assert none of them returns 200 with the file's bytes.


def _seed_a_known_secret(app: Flask) -> tuple[str, bytes]:
    """Write a sentinel file in DATA_DIR (the parent of MEDIA_ROOT).

    A successful `..` escape from MEDIA_ROOT would expose this file, so its
    presence in a response body is a hard traversal proof.
    """
    secret = b"TOP-SECRET-SENTINEL-DO-NOT-LEAK"
    data_dir = app.config["DATA_DIR"]
    secret_path = os.path.join(data_dir, "secret.txt")
    with open(secret_path, "wb") as fh:
        fh.write(secret)
    return secret_path, secret


def test_media_path_traversal_to_data_dir_sentinel_blocked(
    client: FlaskClient, app: Flask
) -> None:
    _, secret = _seed_a_known_secret(app)
    payloads = [
        "/media/../secret.txt",
        "/media/..%2fsecret.txt",
        "/media/%2e%2e/secret.txt",
        "/media/..%2F..%2Fsecret.txt",
        "/media/....//secret.txt",
        "/media/products/../../secret.txt",
    ]
    for url in payloads:
        r = client.get(url)
        assert r.status_code != 200, f"{url} unexpectedly returned 200"
        assert secret not in r.get_data(), f"{url} leaked the sentinel file"


def test_media_path_traversal_to_sqlite_db_blocked(
    client: FlaskClient, app: Flask
) -> None:
    """The SQLite DB lives at DATA_DIR/gluck.db, one level above MEDIA_ROOT."""
    # Make sure the DB file exists on disk.
    with app.app_context():
        from app.factory import db

        db.session.execute(db.text("SELECT 1"))
    payloads = [
        "/media/../gluck.db",
        "/media/..%2fgluck.db",
        "/media/..%2F..%2Fgluck.db",
        "/media/%2e%2e%2fgluck.db",
    ]
    for url in payloads:
        r = client.get(url)
        assert r.status_code != 200, f"{url} unexpectedly returned 200"
        # The SQLite header magic string must never appear in the body.
        assert b"SQLite format 3" not in r.get_data(), f"{url} leaked the DB"


def test_media_path_traversal_to_etc_passwd_blocked(client: FlaskClient) -> None:
    payloads = [
        "/media/../../../../../../etc/passwd",
        "/media/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
        "/media/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        "/media/....//....//....//etc/passwd",
    ]
    for url in payloads:
        r = client.get(url)
        assert r.status_code != 200, f"{url} unexpectedly returned 200"
        body = r.get_data()
        assert b"root:" not in body, f"{url} leaked /etc/passwd"


def test_media_path_traversal_to_app_source_blocked(client: FlaskClient) -> None:
    """A deep `..` chain must not reach the project's Python source either."""
    payloads = [
        "/media/../../app/auth.py",
        "/media/../../../app/factory.py",
        "/media/..%2f..%2f..%2fapp%2froutes.py",
    ]
    for url in payloads:
        r = client.get(url)
        assert r.status_code != 200, f"{url} unexpectedly returned 200"
        assert b"ADMIN_PASSWORD" not in r.get_data(), f"{url} leaked source"


def test_media_absolute_path_not_served(client: FlaskClient) -> None:
    """An absolute path under /media must not bypass MEDIA_ROOT confinement."""
    r = client.get("/media//etc/passwd")
    assert r.status_code != 200
    assert b"root:" not in r.get_data()


def test_media_unknown_file_returns_404_not_500(client: FlaskClient) -> None:
    """A plain missing file inside MEDIA_ROOT is a clean 404 (no stack trace)."""
    r = client.get("/media/products/999999/1/1000.jpg")
    assert r.status_code == 404


# --- Stored XSS ---------------------------------------------------------------
# Jinja autoescaping must neutralize script payloads stored in product fields.

XSS_PAYLOAD = "<script>alert(1)</script>"
XSS_ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"


def _create_product_with_xss(app: Flask) -> int:
    return _create_product(
        app,
        title=f"Bolso {XSS_PAYLOAD}",
        description=f"Linda cartera {XSS_PAYLOAD}",
        is_published=True,
    )


def test_xss_in_title_escaped_on_product_detail(client: FlaskClient, app: Flask) -> None:
    pid = _create_product_with_xss(app)
    r = client.get(f"/producto/{pid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # The escaped literal is present...
    assert XSS_ESCAPED in body
    # ...and no live, executable <script>alert(1)</script> tag is.
    assert XSS_PAYLOAD not in body


def test_xss_in_description_escaped_on_product_detail(
    client: FlaskClient, app: Flask
) -> None:
    pid = _create_product_with_xss(app)
    r = client.get(f"/producto/{pid}")
    body = r.get_data(as_text=True)
    # description is rendered inside white-space:pre-line, still autoescaped.
    assert XSS_ESCAPED in body
    assert XSS_PAYLOAD not in body


def test_xss_in_title_escaped_on_home_grid(client: FlaskClient, app: Flask) -> None:
    _create_product_with_xss(app)
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert XSS_ESCAPED in body
    assert XSS_PAYLOAD not in body


def test_xss_attribute_breakout_in_title_escaped(client: FlaskClient, app: Flask) -> None:
    """The title is also rendered into alt="…" attributes — quotes must escape.

    A payload that closes the attribute and injects an onerror handler must not
    survive as live markup.
    """
    payload = '" onerror="alert(1)'
    pid = _create_product(app, title=f"X{payload}", is_published=True)
    r = client.get("/")
    body = r.get_data(as_text=True)
    # The raw breakout sequence (literal quote + onerror) must not appear; Jinja
    # escapes the double-quote to &#34;.
    assert 'onerror="alert(1)' not in body
    # Detail page too.
    r2 = client.get(f"/producto/{pid}")
    assert 'onerror="alert(1)' not in r2.get_data(as_text=True)


# --- Admin pages must not be indexable ---------------------------------------


def test_admin_login_page_has_noindex_meta(client: FlaskClient) -> None:
    r = client.get(LOGIN_URL)
    assert r.status_code == 200
    assert '<meta name="robots" content="noindex, nofollow">' in r.get_data(as_text=True)


def test_admin_list_page_has_noindex_meta(
    client: FlaskClient, admin_password: str
) -> None:
    _login(client, admin_password)
    r = client.get(LIST_URL)
    assert r.status_code == 200
    assert '<meta name="robots" content="noindex, nofollow">' in r.get_data(as_text=True)


def test_admin_product_form_has_noindex_meta(
    client: FlaskClient, admin_password: str
) -> None:
    _login(client, admin_password)
    r = client.get(NEW_URL)
    assert r.status_code == 200
    assert '<meta name="robots" content="noindex, nofollow">' in r.get_data(as_text=True)


# --- Upload-size guard --------------------------------------------------------


def test_max_content_length_configured_positive(app: Flask) -> None:
    """A positive MAX_CONTENT_LENGTH caps oversized uploads (DoS mitigation)."""
    cap = app.config.get("MAX_CONTENT_LENGTH")
    assert cap is not None
    assert isinstance(cap, int)
    assert cap > 0


def test_max_content_length_rejects_oversized_request(
    auth_client: FlaskClient, app: Flask
) -> None:
    """A request whose Content-Length exceeds the cap is refused with 413.

    We don't actually stream 600MB; we send a body just over a temporarily
    lowered cap to prove the guard is wired into request handling.
    """
    original = app.config["MAX_CONTENT_LENGTH"]
    app.config["MAX_CONTENT_LENGTH"] = 1024  # 1 KiB for this test only
    try:
        big = b"x" * 4096
        r = auth_client.post(
            NEW_URL,
            data={
                "title": "Too big",
                "media": (io.BytesIO(big), "huge.jpg"),
                "order": '["new:0"]',
            },
            content_type="multipart/form-data",
        )
        assert r.status_code == 413
    finally:
        app.config["MAX_CONTENT_LENGTH"] = original


# --- Wrong login never authenticates -----------------------------------------


def test_wrong_login_does_not_create_session(client: FlaskClient) -> None:
    from app.auth import SESSION_KEY

    client.post(LOGIN_URL, data={"password": "definitely-wrong"})
    with client.session_transaction() as sess:
        assert SESSION_KEY not in sess
    # And admin stays out of reach.
    r = client.get(LIST_URL)
    assert r.status_code == 302


def test_empty_login_does_not_create_session(client: FlaskClient) -> None:
    from app.auth import SESSION_KEY

    client.post(LOGIN_URL, data={"password": ""})
    with client.session_transaction() as sess:
        assert SESSION_KEY not in sess


def test_missing_password_field_does_not_authenticate(client: FlaskClient) -> None:
    from app.auth import SESSION_KEY

    # No password field at all -> default "" -> rejected.
    client.post(LOGIN_URL, data={})
    with client.session_transaction() as sess:
        assert SESSION_KEY not in sess


def test_session_cookie_is_httponly(client: FlaskClient, admin_password: str) -> None:
    """The session cookie should be HttpOnly so JS can't read the admin token.

    Flask sets HttpOnly by default; this guards against a regression that turns
    it off (which would let any XSS steal the admin session).
    """
    r = client.post(LOGIN_URL, data={"password": admin_password})
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "session=" in set_cookie
    assert "HttpOnly" in set_cookie


# --- CSRF mitigation (SameSite cookie) ----------------------------------------
# The admin has no token-based CSRF middleware, but the session cookie is
# SameSite=Lax: browsers won't attach it to cross-site POSTs, so a forged form
# from another origin can't act as the logged-in admin. These tests assert that
# control is configured (a regression that loosened it would fail here).


def test_session_cookie_is_samesite_lax_and_httponly(
    client: FlaskClient, admin_password: str
) -> None:
    """The session cookie set at login carries SameSite=Lax and HttpOnly."""
    resp = client.post("/admin/login", data={"password": admin_password})
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "session=" in set_cookie
    lowered = set_cookie.lower()
    assert "samesite=lax" in lowered, f"session cookie not SameSite=Lax: {set_cookie}"
    assert "httponly" in lowered, f"session cookie not HttpOnly: {set_cookie}"


def test_session_cookie_config(app: Flask) -> None:
    """The app is configured with the CSRF-relevant cookie hardening."""
    assert app.config["SESSION_COOKIE_SAMESITE"] in ("Lax", "Strict")
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_session_cookie_secure_toggle(monkeypatch) -> None:
    """SESSION_COOKIE_SECURE follows the env flag (on in production over HTTPS)."""
    from app.factory import create_app

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "1")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    assert create_app().config["SESSION_COOKIE_SECURE"] is True
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")
    assert create_app().config["SESSION_COOKIE_SECURE"] is False
