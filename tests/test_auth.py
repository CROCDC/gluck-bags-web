"""Tests for the single-shared-password admin auth.

Covers the pure helpers in app.auth (check_password / login / logout /
is_logged_in / login_required) and the HTTP login/logout flow exposed by the
admin blueprint.
"""

from __future__ import annotations

import os

from flask import Flask
from flask.testing import FlaskClient

from app import auth
from app.auth import SESSION_KEY

# Paths kept as constants so a route rename surfaces in one place.
LOGIN_URL = "/admin/login"
LOGOUT_URL = "/admin/logout"
LIST_URL = "/admin/"


# --- check_password (unit, needs an app context for current_app.config) -------


def test_check_password_true_for_configured_password(app: Flask, admin_password: str) -> None:
    with app.app_context():
        assert auth.check_password(admin_password) is True


def test_check_password_false_for_wrong_password(app: Flask, admin_password: str) -> None:
    with app.app_context():
        assert auth.check_password("definitely-not-it") is False
        # A near-miss (extra char) must also fail.
        assert auth.check_password(admin_password + "x") is False
        # The empty string is never the configured password here.
        assert auth.check_password("") is False


def _make_app_with_admin_password(value: str | None) -> Flask:
    """Build a fresh app whose ADMIN_PASSWORD env is `value` (None = unset).

    create_app() reads ADMIN_PASSWORD at construction time, so we mutate the
    env around the call and restore it afterwards.
    """
    from app.factory import create_app

    saved = os.environ.get("ADMIN_PASSWORD")
    saved_seed = os.environ.get("SEED_PRODUCTS")
    try:
        if value is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = value
        os.environ["SEED_PRODUCTS"] = "0"
        application = create_app()
        application.testing = True
        return application
    finally:
        if saved is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = saved
        if saved_seed is None:
            os.environ.pop("SEED_PRODUCTS", None)
        else:
            os.environ["SEED_PRODUCTS"] = saved_seed


def test_check_password_false_when_admin_password_empty() -> None:
    """An empty ADMIN_PASSWORD disables login entirely, even for "".

    Otherwise a misconfigured deploy (no password set) would accept an empty
    password and grant admin access to anyone.
    """
    app = _make_app_with_admin_password("")
    with app.app_context():
        assert auth.check_password("") is False
        assert auth.check_password("anything") is False


def test_check_password_false_when_admin_password_unset() -> None:
    """If ADMIN_PASSWORD is unset, config falls back to "" and login is closed."""
    app = _make_app_with_admin_password(None)
    with app.app_context():
        # create_app() defaults missing ADMIN_PASSWORD to "".
        assert app.config["ADMIN_PASSWORD"] == ""
        assert auth.check_password("") is False
        assert auth.check_password("anything") is False


# --- login() / logout() / is_logged_in() (session toggling) -------------------


def test_login_logout_toggle_session(app: Flask) -> None:
    # A bare request context gives us a real (empty) session to mutate.
    with app.test_request_context():
        from flask import session

        assert auth.is_logged_in() is False
        assert SESSION_KEY not in session

        auth.login()
        assert session.get(SESSION_KEY) is True
        assert auth.is_logged_in() is True

        auth.logout()
        assert SESSION_KEY not in session
        assert auth.is_logged_in() is False


def test_logout_is_idempotent(app: Flask) -> None:
    """logout() on an already-anonymous session must not raise (pop default)."""
    with app.test_request_context():
        assert auth.is_logged_in() is False
        auth.logout()  # no KeyError
        assert auth.is_logged_in() is False


def test_is_logged_in_reflects_falsey_session_value(app: Flask) -> None:
    """A falsey session value is treated as not-logged-in."""
    with app.test_request_context():
        from flask import session

        session[SESSION_KEY] = False
        assert auth.is_logged_in() is False
        session[SESSION_KEY] = True
        assert auth.is_logged_in() is True


# --- login_required (anonymous redirect, authenticated pass-through) ----------


def test_login_required_redirects_anonymous_to_login(client: FlaskClient) -> None:
    r = client.get(LIST_URL)
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")


def test_login_required_redirects_anonymous_on_subroute(client: FlaskClient) -> None:
    # Every login-protected view should bounce an anonymous user, not just "/".
    for path in ("/admin/products/new",):
        r = client.get(path)
        assert r.status_code == 302
        assert LOGIN_URL in r.headers.get("Location", "")


def test_login_required_allows_authenticated(auth_client: FlaskClient) -> None:
    r = auth_client.get(LIST_URL)
    assert r.status_code == 200


# --- POST /admin/login (the HTTP login flow) ----------------------------------


def test_post_login_wrong_password_returns_200_with_flash(client: FlaskClient) -> None:
    r = client.post(LOGIN_URL, data={"password": "wrong-password"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "incorrecta" in body.lower()


def test_post_login_wrong_password_does_not_set_session(client: FlaskClient) -> None:
    client.post(LOGIN_URL, data={"password": "wrong-password"})
    # The protected list must still bounce us to login.
    r = client.get(LIST_URL)
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")
    # And the session cookie must not carry the admin flag.
    with client.session_transaction() as sess:
        assert SESSION_KEY not in sess


def test_post_login_empty_password_rejected(client: FlaskClient) -> None:
    r = client.post(LOGIN_URL, data={"password": ""})
    assert r.status_code == 200
    assert "incorrecta" in r.get_data(as_text=True).lower()
    with client.session_transaction() as sess:
        assert SESSION_KEY not in sess


def test_post_login_correct_password_sets_session_and_redirects(
    client: FlaskClient, admin_password: str
) -> None:
    r = client.post(LOGIN_URL, data={"password": admin_password})
    assert r.status_code == 302
    assert r.headers.get("Location", "").rstrip("/").endswith("/admin")
    with client.session_transaction() as sess:
        assert sess.get(SESSION_KEY) is True
    # And the protected list is now reachable.
    assert client.get(LIST_URL).status_code == 200


def test_logged_in_get_login_redirects_to_list(auth_client: FlaskClient) -> None:
    r = auth_client.get(LOGIN_URL)
    assert r.status_code == 302
    assert r.headers.get("Location", "").rstrip("/").endswith("/admin")


def test_logged_in_post_login_redirects_to_list(auth_client: FlaskClient) -> None:
    # Even a POST to login while authenticated short-circuits to the list
    # (the is_logged_in() guard runs before the password check).
    r = auth_client.post(LOGIN_URL, data={"password": "whatever"})
    assert r.status_code == 302
    assert r.headers.get("Location", "").rstrip("/").endswith("/admin")


# --- POST /admin/logout (clearing access) -------------------------------------


def test_logout_clears_access(auth_client: FlaskClient) -> None:
    # Sanity: we start authenticated.
    assert auth_client.get(LIST_URL).status_code == 200

    r = auth_client.post(LOGOUT_URL)
    assert r.status_code == 302
    assert LOGIN_URL in r.headers.get("Location", "")

    # After logout the session flag is gone and the list bounces us again.
    with auth_client.session_transaction() as sess:
        assert SESSION_KEY not in sess
    after = auth_client.get(LIST_URL)
    assert after.status_code == 302
    assert LOGIN_URL in after.headers.get("Location", "")


def test_logout_get_not_allowed(auth_client: FlaskClient) -> None:
    """Logout is a state-changing action, so it must be POST-only (405 on GET)."""
    r = auth_client.get(LOGOUT_URL)
    assert r.status_code == 405


# --- full round trip ----------------------------------------------------------


def test_full_login_then_logout_round_trip(client: FlaskClient, admin_password: str) -> None:
    # anonymous -> blocked
    assert client.get(LIST_URL).status_code == 302
    # login -> in
    assert client.post(LOGIN_URL, data={"password": admin_password}).status_code == 302
    assert client.get(LIST_URL).status_code == 200
    # logout -> out again
    assert client.post(LOGOUT_URL).status_code == 302
    assert client.get(LIST_URL).status_code == 302
