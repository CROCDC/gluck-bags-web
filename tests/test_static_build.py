"""The static build step and the cache-buster that depends on it.

Dimension: static_build. Static assets move to `public/static/` so a CDN serves them
without waking the Python function. That breaks an assumption the cache-buster made: it
used to `os.stat` the very file it was versioning.

This is the failure worth guarding. `SEND_FILE_MAX_AGE_DEFAULT` is one year, and the
`?v=` is the ONLY thing that makes that safe. If the stat fails because the file is no
longer in the deployed bundle, the buster silently emits an unversioned URL and every
client keeps the pre-deploy asset for a year — a stale-CSS bug that looks like a CDN
problem and is not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask, url_for

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_static.py"


MANIFEST_PATH = REPO_ROOT / "app" / "static_manifest.json"


@pytest.fixture(scope="module")
def built():
    """Run the real build script once and yield the manifest it wrote.

    Removed afterwards: a manifest left in the working tree makes local dev version
    static URLs by content hash, so an edited stylesheet keeps its old URL until
    somebody remembers to re-run the build. It is a deploy artifact, not a dev one.
    """
    existing = MANIFEST_PATH.read_bytes() if MANIFEST_PATH.exists() else None
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr

    yield json.loads(MANIFEST_PATH.read_text())

    if existing is None:
        MANIFEST_PATH.unlink(missing_ok=True)
    else:
        MANIFEST_PATH.write_bytes(existing)


# --- the build script ---------------------------------------------------------


class TestBuild:
    def test_publishes_static_under_public(self, built) -> None:
        """Vercel serves `public/**` from the CDN at the same paths `url_for('static')`
        already generates, so the copy has to keep the `static/` prefix."""
        assert (REPO_ROOT / "public" / "static" / "css" / "styles.css").is_file()

    def test_copies_the_heavy_assets_the_function_should_never_serve(self, built) -> None:
        video = REPO_ROOT / "public" / "static" / "video"

        assert video.is_dir()
        assert any(video.iterdir())

    def test_manifest_keys_match_url_for_filenames(self, built) -> None:
        """The key has to be exactly what `url_for('static', filename=...)` is passed,
        "/" separated, or every lookup misses and silently falls through."""
        assert "css/styles.css" in built
        assert all("\\" not in key for key in built)

    def test_manifest_values_are_content_hashes(self, built) -> None:
        """A hash, not an mtime: a rebuild that changes nothing must leave the URL
        alone, or every deploy would evict the CDN and every browser cache."""
        first = built["css/styles.css"]
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        second = json.loads((REPO_ROOT / "app" / "static_manifest.json").read_text())

        assert second["css/styles.css"] == first

    def test_fonts_are_not_versioned(self, built) -> None:
        """Fonts are referenced from CSS `url()` with no `?v=`; versioning the preload
        URL would make it differ from the @font-face URL and fetch the font twice."""
        assert not [key for key in built if key.endswith((".woff2", ".woff", ".ttf"))]
        # They are still published — just unversioned.
        assert (REPO_ROOT / "public" / "static" / "fonts").is_dir()

    def test_does_not_publish_local_junk(self, built) -> None:
        """.DS_Store on a public CDN leaks the directory listing of the build machine."""
        assert not list((REPO_ROOT / "public").rglob(".DS_Store"))
        assert not [key for key in built if ".DS_Store" in key]


# --- the cache-buster ---------------------------------------------------------


class TestCacheBuster:
    def test_uses_the_manifest_hash_when_one_exists(self, app: Flask, built) -> None:
        with app.test_request_context():
            url = url_for("static", filename="css/styles.css")

        assert url.endswith(f"?v={built['css/styles.css']}")

    def test_still_versions_when_the_file_is_not_in_the_bundle(
        self, app: Flask, built, monkeypatch
    ) -> None:
        """The whole point: the CDN owns the file, so the app cannot stat it. Without
        the manifest the URL would lose its `?v=` and a one-year cache would go stale."""
        monkeypatch.setattr(app, "static_folder", "/nonexistent")

        with app.test_request_context():
            url = url_for("static", filename="css/styles.css")

        assert "?v=" in url

    def test_falls_back_to_mtime_without_a_manifest(self, tmp_path, monkeypatch) -> None:
        """Local dev has no build step; mtime is the better answer there because it
        changes the moment a file is saved."""
        monkeypatch.setattr("app.factory._load_static_manifest", lambda: {})
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SEED_PRODUCTS", "0")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key")
        from app.factory import create_app

        application = create_app()

        with application.test_request_context():
            url = url_for("static", filename="css/styles.css")

        version = url.split("?v=")[1]
        assert version.isdigit(), url

    def test_fonts_stay_unversioned_through_the_manifest_path(
        self, app: Flask, built
    ) -> None:
        # A font that really exists: a missing file gets no `?v=` either way, which
        # would make this pass even if fonts were being versioned.
        font = next(p for p in (REPO_ROOT / "app" / "static" / "fonts").glob("*.woff2"))
        filename = f"fonts/{font.name}"
        assert (REPO_ROOT / "public" / "static" / filename).is_file()

        with app.test_request_context():
            url = url_for("static", filename=filename)

        assert "?v=" not in url

    def test_inline_css_still_reads_from_the_bundle(self, client) -> None:
        """`inline_css` opens the CSS at request time, so app/static/css must stay in
        the function bundle even though the CDN also serves a copy."""
        response = client.get("/")

        assert response.status_code == 200
        assert b"<style>" in response.data
