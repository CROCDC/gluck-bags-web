"""Fixtures compartidos para los tests.

Levanta el sitio Flask en un servidor WSGI real (en un thread aparte) sobre un
puerto libre, para que Playwright pueda visitarlo como lo haría un navegador de
verdad. Esto es necesario para medir performance: el test client de Flask no
sirve recursos estáticos por HTTP ni ejecuta JavaScript.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright
from werkzeug.serving import make_server

from app import app as flask_app


def _free_port() -> int:
    """Devuelve un puerto TCP libre asignado por el sistema operativo."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server() -> Iterator[str]:
    """Arranca el sitio en un servidor WSGI real y devuelve su URL base."""
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
    """Una instancia de Chromium headless reutilizable por toda la sesión."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()
