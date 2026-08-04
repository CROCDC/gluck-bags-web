"""Shared fixtures for the tests.

Brings up the Flask site on a real WSGI server (in a separate thread) on a free
port, so Playwright can visit it like a real browser would. This is needed to
measure performance: Flask's test client neither serves static resources over
HTTP nor runs JavaScript.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright
from werkzeug.serving import make_server

# Hermetic test environment: the module-level app (used by the perf/live_server
# tests) gets its OWN seeded data dir under the OS temp, never the dev ./instance.
# Set BEFORE importing the app, since create_app() reads these at import time.
ADMIN_PASSWORD = "test-admin-pw"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="gluck-tests-session-")
os.environ["SEED_PRODUCTS"] = "1"
os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("SECRET_KEY", "test-secret-key")

# Keep the suite hermetic from a populated .env: with real Tienda Nube credentials
# present, load_dotenv() would leak them into the process and flip the
# "not_configured" branches the tests assert. Setting them empty (not deleting)
# means load_dotenv(override=False) won't repopulate them; the code reads empty as
# "missing". Tests that need credentials set them via monkeypatch. CATALOG_SOURCE
# is pinned too: the dev .env says "tiendanube" since the prod flip, which would
# make every admin-catalogue test serve an empty TN mirror.
for _tn_var in ("TN_STORE_ID", "TN_ACCESS_TOKEN", "TN_CLIENT_ID", "TN_CLIENT_SECRET"):
    os.environ[_tn_var] = ""
# Pinned to the admin catalogue so the suite needs no Tienda Nube credentials. Set
# TEST_CATALOG_SOURCE=tiendanube to run the content/editor files the way PRODUCTION
# runs — that surface must not depend on where products come from, and pinning this
# meant nothing ever exercised it under the real config.
os.environ["CATALOG_SOURCE"] = os.environ.get("TEST_CATALOG_SOURCE", "admin")

from app import app as flask_app


def pytest_addoption(parser) -> None:
    """`--snapshot-update` rewrites the public-copy snapshots (see
    tests/test_public_copy_golden.py) instead of asserting against them."""
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Rewrite the public-copy snapshots in tests/golden/.",
    )


def _free_port() -> int:
    """Returns a free TCP port assigned by the operating system."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server() -> Iterator[str]:
    """Returns the base URL to test.

    If PERF_TARGET_URL is set, we test that URL directly (e.g. staging/prod) and
    don't start a local server — handy to catch issues that only exist behind the
    real reverse proxy / CDN (caching headers, compression). Combine it with
    PERF_CHECK_PROD_HEADERS=1 to enable the header tests:
        PERF_TARGET_URL=https://gluckbags.com PERF_CHECK_PROD_HEADERS=1 \\
            pytest tests/test_performance.py -v
    Otherwise we spin up the Flask app on a real WSGI server (local default).
    """
    external = os.environ.get("PERF_TARGET_URL")
    if external:
        yield external.rstrip("/")
        return
    port = _free_port()
    # threaded=True so resources load in parallel (the single-threaded dev server
    # serializes requests, which badly skews Lighthouse's simulated throttling).
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """A headless Chromium instance reusable across the whole session."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


# --- Isolated app/client fixtures for unit & integration tests ----------------
# These build a FRESH app per test with its own empty (unseeded) temp data dir,
# so admin/CRUD tests can't pollute each other or the seeded perf app.

@pytest.fixture
def admin_password() -> str:
    return ADMIN_PASSWORD


@pytest.fixture
def temp_app(tmp_path, monkeypatch):
    """A fresh Flask app with an isolated, empty data dir (no seeded products)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    from app.factory import create_app

    application = create_app()
    application.testing = True
    return application


@pytest.fixture
def app(temp_app):
    return temp_app


@pytest.fixture
def client(temp_app):
    return temp_app.test_client()


@pytest.fixture
def auth_client(temp_app):
    """A test client already logged into the admin."""
    c = temp_app.test_client()
    c.post("/admin/login", data={"password": ADMIN_PASSWORD})
    return c


@pytest.fixture(scope="module")
def admin_live_server() -> Iterator[tuple[str, str]]:
    """An ISOLATED seeded app served over HTTP for E2E admin tests.

    Separate data dir + DB + media from the perf `live_server`, so creating
    products in E2E can't affect the performance budgets. Yields (base_url, password).
    """
    import shutil

    tmp = tempfile.mkdtemp(prefix="gluck-e2e-")
    saved = {k: os.environ.get(k) for k in ("DATA_DIR", "SEED_PRODUCTS", "ADMIN_PASSWORD", "SECRET_KEY")}
    os.environ["DATA_DIR"] = tmp
    os.environ["SEED_PRODUCTS"] = "1"
    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["SECRET_KEY"] = "test-secret-key"
    from app.factory import create_app

    application = create_app()
    port = _free_port()
    server = make_server("127.0.0.1", port, application, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", ADMIN_PASSWORD
    finally:
        server.shutdown()
        thread.join(timeout=5)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)
