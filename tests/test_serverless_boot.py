"""Booting and serving under the serverless configuration.

Dimension: serverless_boot. Every other test runs the app the way the Docker deploy runs
it: a writable data dir, SQLite, local media, schema created at boot. The Vercel shape
inverts all four at once, and the failures that combination produces are import-time
ones — the app never comes up, so no request-level test can catch them.

What is pinned here is that the app boots and serves a page with:

- a READ-ONLY filesystem (no data dir to create),
- `MEDIA_STORE=blob` (media URLs point at the CDN, `/media` serves nothing),
- `AUTO_INIT_DB=0` (no DDL in the request path),
- `SECRET_KEY` from the environment (nowhere to persist one).
"""

from __future__ import annotations

import os

import pytest

from fake_blob import FakeBlobApi


@pytest.fixture
def read_only_dir(tmp_path):
    """A directory the process cannot write to, like a deployment bundle."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores the read-only bit")
    path = tmp_path / "bundle"
    path.mkdir()
    path.chmod(0o500)
    yield path
    path.chmod(0o700)


@pytest.fixture
def serverless_app(read_only_dir, tmp_path, monkeypatch):
    """The app as Vercel would build it, with Blob answered in memory.

    DATABASE_URL points at a file OUTSIDE the read-only bundle. The engine is not what
    is under test here — what matters is that the data lives somewhere other than the
    deployment, which is the property that breaks the boot path.
    """
    api = FakeBlobApi()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'remote.db'}")
    monkeypatch.setenv("DATA_DIR", str(read_only_dir / "data"))
    monkeypatch.setenv("MEDIA_STORE", "blob")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", api.token)
    monkeypatch.setenv("AUTO_INIT_DB", "0")
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("SECRET_KEY", "set-in-the-environment")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")

    from app.factory import create_app

    application = create_app()
    application.testing = True
    # Route the adapter's HTTP at the fake, after construction so the real token
    # parsing still runs.
    application.extensions["media_store"]._session = api
    return application, api


class TestBoot:
    def test_builds_with_a_read_only_filesystem(self, serverless_app) -> None:
        """`create_app` used to mkdir the data dir unconditionally, which is an
        ImportError-shaped failure on a read-only bundle: the app never comes up."""
        application, _ = serverless_app

        assert application is not None

    def test_uses_the_blob_store(self, serverless_app) -> None:
        from app.services.media_store_blob import BlobMediaStore

        application, _ = serverless_app

        assert isinstance(application.extensions["media_store"], BlobMediaStore)

    def test_creates_no_tables_at_boot(self, serverless_app) -> None:
        from sqlalchemy import inspect

        from app.factory import db

        application, _ = serverless_app
        with application.app_context():
            assert inspect(db.engine).get_table_names() == []

    def test_refuses_to_boot_without_a_secret_key(self, read_only_dir, monkeypatch) -> None:
        """The alternative is a new random key per cold start, which logs the admin out
        on every one of them while the site looks perfectly healthy."""
        api = FakeBlobApi()
        monkeypatch.setenv("DATA_DIR", str(read_only_dir / "data"))
        monkeypatch.setenv("MEDIA_STORE", "blob")
        monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", api.token)
        monkeypatch.setenv("AUTO_INIT_DB", "0")
        monkeypatch.delenv("SECRET_KEY", raising=False)

        from app.factory import create_app

        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            create_app()

    def test_blob_store_needs_its_token(self, read_only_dir, monkeypatch) -> None:
        """Failing loudly beats booting into a store that 401s on the first upload."""
        monkeypatch.setenv("DATA_DIR", str(read_only_dir / "data"))
        monkeypatch.setenv("MEDIA_STORE", "blob")
        monkeypatch.setenv("SECRET_KEY", "x")
        monkeypatch.setenv("AUTO_INIT_DB", "0")
        monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

        from app.factory import create_app
        from app.services.media_store_blob import BlobError

        with pytest.raises(BlobError, match="BLOB_READ_WRITE_TOKEN"):
            create_app()


class TestServes:
    def test_home_renders(self, serverless_app) -> None:
        application, _ = serverless_app
        # The tables exist by the time a request arrives in production (`flask init-db`
        # ran at deploy time); create them here so this exercises rendering, not DDL.
        application.test_cli_runner().invoke(args=["init-db"])

        response = application.test_client().get("/")

        assert response.status_code == 200

    def test_media_route_serves_nothing(self, serverless_app) -> None:
        """Blob's CDN owns the bytes; the function must not pretend to serve them from
        a MEDIA_ROOT that does not exist."""
        application, _ = serverless_app

        assert application.test_client().get("/media/products/1/1/400.jpg").status_code == 404

    def test_media_urls_point_at_the_cdn(self, serverless_app) -> None:
        from app.models import Media

        application, api = serverless_app
        application.test_cli_runner().invoke(args=["init-db"])

        with application.app_context():
            media = Media(kind="image", path="products/1/1", widths=[400], product_id=1)
            url = media.thumb_url

        assert url.startswith(f"https://{api.store_id}.public.blob.vercel-storage.com/")

    def test_editor_uploads_go_to_blob(self, serverless_app) -> None:
        from app.content import BlobFileStore

        application, _ = serverless_app
        with application.app_context():
            from sitecopy.state import current_state

            assert isinstance(current_state().file_store, BlobFileStore)

    def test_image_processing_lands_in_blob(self, serverless_app, tmp_path) -> None:
        """End to end through the real Pillow pipeline: the staging dir is local, the
        variants end up in object storage."""
        import io

        from PIL import Image

        from app.services import media_service

        application, api = serverless_app
        source = tmp_path / "src.jpg"
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), (10, 20, 30)).save(buf, "JPEG")
        source.write_bytes(buf.getvalue())

        with application.app_context():
            info = media_service.process_image(str(source), product_id=5, media_id=9)

        assert info["path"] == "products/5/9"
        stored = {p for p in api.blobs if p.startswith("products/5/9/")}
        assert "products/5/9/400.jpg" in stored
        assert "products/5/9/og.jpg" in stored
