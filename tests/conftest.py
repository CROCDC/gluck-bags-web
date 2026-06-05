"""Shared fixtures for the tests.

Brings up the Flask site on a real WSGI server (in a separate thread) on a free
port, so Playwright can visit it like a real browser would. This is needed to
measure performance: Flask's test client neither serves static resources over
HTTP nor runs JavaScript.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright
from werkzeug.serving import make_server

# ADAPT THIS IMPORT to your project's Flask entrypoint:
#   - module-level app object:   from app import app as flask_app
#   - app factory:               from app import create_app; flask_app = create_app()
#   - package:                   from myproject import app as flask_app
from app import app as flask_app


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
    server = make_server("127.0.0.1", port, flask_app)
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
