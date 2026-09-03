"""Admin uploads that bypass the request-body limit.

Dimension: direct_upload. A serverless function's request body is capped at 4.5 MB — a
single phone photo clears that, and a video is not close — so the admin cannot post its
files through the app. Instead the browser PUTs each file straight into object storage
with a short-lived token, and the form posts only the pathnames; the server reads those
back, runs the same Pillow/ffmpeg pipeline, and deletes the staged original.

The security-shaped tests are the point here. The token is handed to a browser, so its
constraints have to be inside the signed payload, and the pathname the form posts back
decides what the server reads — a value that escapes the staging area would let a logged
-in admin's form pull any object in the store into a product gallery.
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

from fake_blob import FakeBlobApi

UPLOAD_TOKEN_URL = "/admin/media/upload-token"


@pytest.fixture
def blob_app(tmp_path, monkeypatch):
    """An admin app whose media store is Blob, answered in memory."""
    api = FakeBlobApi()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEDIA_STORE", "blob")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", api.token)
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    from app.factory import create_app

    application = create_app()
    application.testing = True
    application.extensions["media_store"]._session = api
    return application, api


@pytest.fixture
def blob_client(blob_app):
    application, api = blob_app
    client = application.test_client()
    client.post("/admin/login", data={"password": "test-admin-pw"})
    return application, api, client


def _jpeg_bytes(width: int = 800, height: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _decode_token(token: str) -> dict:
    inner = base64.b64decode(token.split("_", 4)[4]).decode()
    _signature, _, payload = inner.partition(".")
    return json.loads(base64.b64decode(payload))


# --- the token endpoint -------------------------------------------------------


class TestUploadToken:
    def test_requires_login(self, blob_app) -> None:
        application, _ = blob_app

        response = application.test_client().post(
            UPLOAD_TOKEN_URL, json={"filename": "foto.jpg"}
        )

        assert response.status_code in (302, 401, 403)

    def test_absent_on_the_filesystem_backend(self, client, auth_client) -> None:
        """Nothing to upload to, and the ordinary multipart form already works — the
        route must not exist rather than hand out a token for a store that isn't there."""
        assert auth_client.post(UPLOAD_TOKEN_URL, json={"filename": "foto.jpg"}).status_code == 404

    def test_issues_a_token_for_a_supported_file(self, blob_client) -> None:
        _app, _api, client = blob_client

        response = client.post(UPLOAD_TOKEN_URL, json={"filename": "foto.jpg"})

        assert response.status_code == 200
        body = response.get_json()
        assert body["token"].startswith("vercel_blob_client_")
        assert body["pathname"].startswith("uploads/")
        assert body["uploadUrl"].startswith("https://blob.vercel-storage.com/")

    def test_rejects_an_unsupported_format(self, blob_client) -> None:
        _app, _api, client = blob_client

        response = client.post(UPLOAD_TOKEN_URL, json={"filename": "malware.exe"})

        assert response.status_code == 400

    def test_pathname_is_unique_per_request(self, blob_client) -> None:
        """Two files queued together must not collide on one staging path."""
        _app, _api, client = blob_client

        first = client.post(UPLOAD_TOKEN_URL, json={"filename": "a.jpg"}).get_json()
        second = client.post(UPLOAD_TOKEN_URL, json={"filename": "a.jpg"}).get_json()

        assert first["pathname"] != second["pathname"]

    def test_constraints_travel_inside_the_signed_payload(self, blob_client) -> None:
        """The browser holds this token, so a constraint it could edit is no constraint.
        Size, content types and the destination path are all signed."""
        _app, _api, client = blob_client

        body = client.post(UPLOAD_TOKEN_URL, json={"filename": "foto.jpg"}).get_json()
        payload = _decode_token(body["token"])

        assert payload["pathname"] == body["pathname"]
        assert payload["maximumSizeInBytes"] == body["maxBytes"]
        assert "image/jpeg" in payload["allowedContentTypes"]
        assert "video/mp4" not in payload["allowedContentTypes"]
        assert payload["addRandomSuffix"] is False

    def test_video_and_image_get_different_content_types(self, blob_client) -> None:
        _app, _api, client = blob_client

        image = _decode_token(
            client.post(UPLOAD_TOKEN_URL, json={"filename": "a.jpg"}).get_json()["token"]
        )
        video = _decode_token(
            client.post(UPLOAD_TOKEN_URL, json={"filename": "a.mp4"}).get_json()["token"]
        )

        assert "image/jpeg" in image["allowedContentTypes"]
        assert "video/mp4" in video["allowedContentTypes"]
        assert "image/jpeg" not in video["allowedContentTypes"]

    def test_token_expires(self, blob_client) -> None:
        import time

        _app, _api, client = blob_client

        payload = _decode_token(
            client.post(UPLOAD_TOKEN_URL, json={"filename": "a.jpg"}).get_json()["token"]
        )

        assert payload["validUntil"] > time.time() * 1000
        assert payload["validUntil"] < (time.time() + 24 * 3600) * 1000


# --- ingesting what the browser uploaded --------------------------------------


class TestIngest:
    def test_processes_a_staged_upload_into_the_gallery(self, blob_client) -> None:
        from app.models import Product

        application, api, client = blob_client
        api.blobs["uploads/staged.jpg"] = (_jpeg_bytes(), "image/jpeg")

        response = client.post(
            "/admin/products/new",
            data={
                "title": "Bolso subido",
                "is_published": "on",
                "order": json.dumps(["new:0"]),
                "media_uploaded": json.dumps(
                    [{"pathname": "uploads/staged.jpg", "filename": "foto.jpg"}]
                ),
            },
            follow_redirects=False,
        )

        assert response.status_code in (302, 303), response.data[:400]
        with application.app_context():
            product = Product.query.filter_by(title="Bolso subido").one()
            assert len(product.media) == 1
            assert product.media[0].kind == "image"
        assert any(p.startswith("products/") and p.endswith("400.jpg") for p in api.blobs)

    def test_deletes_the_staged_original(self, blob_client) -> None:
        """It has served its purpose; keeping it bills for a second copy of every photo
        in the store, forever."""
        _app, api, client = blob_client
        api.blobs["uploads/staged.jpg"] = (_jpeg_bytes(), "image/jpeg")

        client.post(
            "/admin/products/new",
            data={
                "title": "Bolso",
                "order": json.dumps(["new:0"]),
                "media_uploaded": json.dumps(
                    [{"pathname": "uploads/staged.jpg", "filename": "foto.jpg"}]
                ),
            },
        )

        assert "uploads/staged.jpg" not in api.blobs

    @pytest.mark.parametrize(
        "pathname",
        [
            "products/1/1/400.jpg",  # an existing gallery file
            "uploads/../products/1/1/400.jpg",
            "sitecopy-uploads/secret.png",
            "/etc/passwd",
        ],
    )
    def test_refuses_a_pathname_outside_the_staging_area(
        self, blob_client, pathname: str
    ) -> None:
        """The posted pathname decides what the server reads back, so it is confined to
        the staging prefix — otherwise the form could pull any object into a gallery."""
        from app.models import Product

        application, api, client = blob_client
        api.blobs[pathname.lstrip("/")] = (_jpeg_bytes(), "image/jpeg")

        client.post(
            "/admin/products/new",
            data={
                "title": "Bolso hostil",
                "order": json.dumps(["new:0"]),
                "media_uploaded": json.dumps(
                    [{"pathname": pathname, "filename": "foto.jpg"}]
                ),
            },
        )

        with application.app_context():
            product = Product.query.filter_by(title="Bolso hostil").first()
            assert product is not None
            assert product.media == []

    def test_a_multipart_file_still_wins(self, blob_client) -> None:
        """A browser that could not reach the store must still save the ordinary way."""
        from app.models import Product

        application, _api, client = blob_client

        client.post(
            "/admin/products/new",
            data={
                "title": "Bolso multipart",
                "order": json.dumps(["new:0"]),
                "media_uploaded": json.dumps([{"pathname": "uploads/x.jpg", "filename": "x.jpg"}]),
                "media": (io.BytesIO(_jpeg_bytes()), "directo.jpg"),
            },
            content_type="multipart/form-data",
        )

        with application.app_context():
            product = Product.query.filter_by(title="Bolso multipart").one()
            assert len(product.media) == 1

    def test_a_missing_staged_file_is_reported_not_crashed(self, blob_client) -> None:
        application, _api, client = blob_client

        response = client.post(
            "/admin/products/new",
            data={
                "title": "Bolso faltante",
                "order": json.dumps(["new:0"]),
                "media_uploaded": json.dumps(
                    [{"pathname": "uploads/gone.jpg", "filename": "foto.jpg"}]
                ),
            },
        )

        assert response.status_code < 500
        with application.app_context():
            from app.models import Product

            product = Product.query.filter_by(title="Bolso faltante").first()
            assert product is not None
            assert product.media == []


# --- the form itself ----------------------------------------------------------


class TestForm:
    def test_blob_backend_advertises_the_upload_route(self, blob_client) -> None:
        _app, _api, client = blob_client

        html = client.get("/admin/products/new").data.decode()

        assert 'data-upload-token-url="/admin/media/upload-token"' in html
        assert 'name="media_uploaded"' in html

    def test_local_backend_does_not(self, auth_client) -> None:
        """With a filesystem there is nothing to upload to, and the multipart POST the
        form already does is the right thing."""
        html = auth_client.get("/admin/products/new").data.decode()

        assert "data-upload-token-url" not in html
