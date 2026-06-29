"""Integration tests for the app-level error handlers wired in app.factory.

Covers behaviour you can only observe with the handlers actually RUNNING (i.e. a
NON-testing app, where Flask doesn't re-raise into the test): the friendly 500
page + session rollback, the 413 over-cap-upload message, a clean 404 for
unknown routes, and a smoke sweep proving no zero-arg GET route 5xxs on boot.
"""

from __future__ import annotations

import io

import pytest
from flask import Flask
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from werkzeug.exceptions import InternalServerError

from app.factory import db
from app.repositories import ProductRepository

ADMIN_PASSWORD = "test-admin-pw"

# The exact strings the handlers return; assert on them so a message edit is a
# deliberate change, not a silent regression.
ERROR_500_BODY = "Ocurrió un error al procesar la solicitud. Probá de nuevo."
ERROR_413_BODY = "El archivo es demasiado grande."


def _make_app(tmp_path, monkeypatch, *, testing: bool) -> Flask:
    """Build a fresh app on an isolated empty data dir.

    `testing=False` is what makes the error handlers actually run: with
    app.testing (and PROPAGATE_EXCEPTIONS) on, Flask re-raises into the test
    client instead of invoking @errorhandler(500).
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "k")
    from app.factory import create_app

    application = create_app()
    application.testing = testing
    return application


# --- 500 handler: friendly body + session rollback ----------------------------


def test_500_handler_returns_friendly_body(tmp_path, monkeypatch) -> None:
    """An unhandled error on "/" surfaces the friendly Spanish body (not a raw
    traceback) with a 500 status — proving the @errorhandler(500) actually ran."""
    app = _make_app(tmp_path, monkeypatch, testing=False)
    # Belt-and-suspenders: ensure Flask routes the exception to the handler
    # instead of propagating it into the test client.
    app.config["PROPAGATE_EXCEPTIONS"] = False

    def _boom() -> list:
        raise RuntimeError("kaboom")

    # Patch the method on the class object the index route resolves at call time.
    monkeypatch.setattr(ProductRepository, "get_published", staticmethod(_boom))

    client = app.test_client()
    resp = client.get("/")

    # If the raw RuntimeError reached us, the app is still propagating and the
    # handler never ran — fail loudly here rather than masking it.
    assert resp.status_code == 500, "expected the 500 handler to run, got a raised/other status"
    # Exact-equality on the body: a message edit is then a deliberate change, and a
    # leaked traceback (the failure mode this handler exists to prevent) won't match.
    assert resp.get_data(as_text=True) == ERROR_500_BODY


def test_500_handler_rolls_back_the_session(tmp_path, monkeypatch) -> None:
    """The 500 handler calls db.session.rollback() so a request that died mid-
    transaction doesn't leave the (scoped) session poisoned for the next caller.

    We can't observe this through the test client: Flask-SQLAlchemy's app-context
    teardown removes the session at request end, so a *new* app_context() always
    starts clean regardless of the handler. So we poison the session for real
    (a failed flush -> PendingRollbackError) and invoke the app's REGISTERED 500
    handler in that same context, then assert the session recovered. If the
    handler stops rolling back, the post-handler query raises PendingRollbackError
    and this test fails.
    """
    app = _make_app(tmp_path, monkeypatch, testing=False)
    handler = next(iter(app.error_handler_spec[None][500].values()))

    from app.models import Product

    with app.app_context():
        # A NOT NULL violation fails the flush, putting the session into the
        # "transaction has been rolled back" guard state.
        db.session.add(Product(title=None, position=0))
        with pytest.raises(IntegrityError):
            db.session.flush()

        # Precondition: the session really is poisoned (so the post-handler pass
        # below can't be a vacuous green).
        with pytest.raises(PendingRollbackError):
            Product.query.count()

        body, status = handler(InternalServerError())
        assert status == 500
        assert body == ERROR_500_BODY

        # The handler's rollback cleared the failed transaction: this now works.
        assert Product.query.count() == 0


# --- 413 handler: over-cap upload message -------------------------------------


def test_413_handler_returns_exact_message(tmp_path, monkeypatch) -> None:
    """A body larger than MAX_CONTENT_LENGTH is rejected with the handler's exact
    message. We temporarily lower the cap to ~1KB and restore it in a finally."""
    app = _make_app(tmp_path, monkeypatch, testing=False)
    client = app.test_client()
    client.post("/admin/login", data={"password": ADMIN_PASSWORD})

    original_cap = app.config["MAX_CONTENT_LENGTH"]
    app.config["MAX_CONTENT_LENGTH"] = 1024  # 1KB
    try:
        oversized = io.BytesIO(b"x" * 4096)  # 4KB payload > 1KB cap
        resp = client.post(
            "/admin/products/new",
            data={"title": "Big", "media": (oversized, "big.bin")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 413
        assert resp.get_data(as_text=True) == ERROR_413_BODY
    finally:
        app.config["MAX_CONTENT_LENGTH"] = original_cap


# --- Unknown route: clean 404 (not a 500) -------------------------------------


def test_unknown_route_returns_clean_404(tmp_path, monkeypatch) -> None:
    app = _make_app(tmp_path, monkeypatch, testing=False)
    client = app.test_client()
    resp = client.get("/definitely-not-a-real-route")
    assert resp.status_code == 404
    # A 404 must NOT trip the 500 handler.
    assert resp.get_data(as_text=True) != ERROR_500_BODY


# --- Smoke: no zero-arg GET route 5xxs on boot --------------------------------


def test_no_zero_arg_get_route_5xxs(tmp_path, monkeypatch) -> None:
    """Every GET route that takes no path args must be reachable without crashing.

    Guards that the app boots and each static-path GET endpoint renders/redirects
    cleanly. Any of 200/302/401/403/404/405 is acceptable; only 5xx fails.
    """
    app = _make_app(tmp_path, monkeypatch, testing=False)
    client = app.test_client()

    checked: list[str] = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        if "GET" not in (rule.methods or set()):
            continue
        if rule.arguments:  # skip routes needing a path parameter
            continue
        path = str(rule.rule)
        resp = client.get(path)
        assert resp.status_code < 500, f"{path} -> {resp.status_code} (5xx on a static GET route)"
        checked.append(path)

    # Sanity: the sweep actually exercised something (e.g. "/", "/robots.txt").
    assert checked, "no zero-arg GET routes were found to check"
    assert "/" in checked


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
