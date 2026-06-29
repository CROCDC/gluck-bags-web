"""Tests for the canonical-host enforcement wired in app.factory.

Dimension: indexability. In production FORCE_CANONICAL_HOST is on: every request
on a non-canonical host (www, the nexttech alias) or over http is 301-redirected
to SITE_URL, preserving path + query, and responses carry HSTS. The flag is OFF
by default so the local/test client (host 'localhost') is never redirected — this
file builds a dedicated app with the flag on (the pattern used by the Umami test).
"""

from __future__ import annotations

import pytest
from flask import Flask


def _app_with_canonical(tmp_path, monkeypatch, **env: str) -> Flask:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    monkeypatch.setenv("SECRET_KEY", "k")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from app.factory import create_app

    application = create_app()
    application.testing = True
    return application


# --- Flag ON: non-canonical host/scheme -> 301 to SITE_URL --------------------


@pytest.mark.parametrize(
    "host, proto",
    [
        ("www.gluckbags.com", "https"),  # www -> apex
        ("gluck.nexttech.com.ar", "https"),  # the staging alias -> apex
        ("gluckbags.com", "http"),  # http -> https
    ],
)
def test_non_canonical_redirects_301(tmp_path, monkeypatch, host, proto) -> None:
    app = _app_with_canonical(
        tmp_path, monkeypatch, SITE_URL="https://gluckbags.com", FORCE_CANONICAL_HOST="1"
    )
    resp = app.test_client().get(
        "/producto/1?utm=x", base_url=f"https://{host}", headers={"X-Forwarded-Proto": proto}
    )
    assert resp.status_code == 301
    # Path + query are preserved on the canonical origin.
    assert resp.headers["Location"] == "https://gluckbags.com/producto/1?utm=x"


def test_canonical_host_passes_through_with_hsts(tmp_path, monkeypatch) -> None:
    """A request already on the canonical host + https is served (200) and carries
    the HSTS header."""
    app = _app_with_canonical(
        tmp_path, monkeypatch, SITE_URL="https://gluckbags.com", FORCE_CANONICAL_HOST="1"
    )
    resp = app.test_client().get(
        "/", base_url="https://gluckbags.com", headers={"X-Forwarded-Proto": "https"}
    )
    assert resp.status_code == 200
    assert resp.headers.get("Strict-Transport-Security", "").startswith("max-age=")


# --- Flag OFF (default): no redirect, no HSTS ---------------------------------


def test_flag_off_does_not_redirect(tmp_path, monkeypatch) -> None:
    """With the flag off (local/test default) even an 'other' host is served as-is,
    so the test client and local dev are never bounced."""
    app = _app_with_canonical(tmp_path, monkeypatch)  # no SITE_URL / flag overrides
    assert app.config["FORCE_CANONICAL_HOST"] is False
    resp = app.test_client().get("/", base_url="http://www.gluckbags.com")
    assert resp.status_code == 200
    assert "Strict-Transport-Security" not in resp.headers
