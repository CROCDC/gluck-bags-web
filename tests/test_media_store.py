"""Contract tests for the media storage port (app/services/media_store.py).

The port is the seam the Vercel migration hangs on: processing writes into a staging
directory and hands it to a store, so the same Pillow/ffmpeg pipeline can land on a
local filesystem or on object storage. These tests pin the behaviour every backend has
to honour, so a second adapter can be checked against the same expectations rather
than against a reading of the local one.

`LocalMediaStore` is exercised directly (no app context needed) plus through the app
factory, which is what wires the store into `app.extensions`.
"""

from __future__ import annotations

import os

import pytest

from app.services.media_store import (
    MEDIA_URL_PREFIX,
    SOURCE_LOCAL,
    LocalMediaStore,
    get_store,
    is_local,
    source,
)


@pytest.fixture
def store(tmp_path) -> LocalMediaStore:
    return LocalMediaStore(str(tmp_path / "media"), MEDIA_URL_PREFIX)


def _staging(tmp_path, name: str, files: dict[str, bytes]) -> str:
    """Build a staging directory holding `files`, the way processing would."""
    path = tmp_path / name
    path.mkdir(parents=True)
    for filename, data in files.items():
        (path / filename).write_bytes(data)
    return str(path)


# --- publish -----------------------------------------------------------------


class TestPublish:
    def test_moves_every_file_under_rel_dir(self, store, tmp_path) -> None:
        staging = _staging(tmp_path, "stage", {"400.jpg": b"a", "400.webp": b"b"})

        store.publish("products/1/7", staging)

        assert store.list_dir("products/1/7") == {"400.jpg", "400.webp"}
        assert store.open("products/1/7/400.jpg").read() == b"a"

    def test_creates_intermediate_directories(self, store, tmp_path) -> None:
        staging = _staging(tmp_path, "stage", {"video.mp4": b"v"})

        store.publish("products/42/deep/nested", staging)

        assert store.list_dir("products/42/deep/nested") == {"video.mp4"}

    def test_replaces_rather_than_merges(self, store, tmp_path) -> None:
        """A reprocess must not leave a stale variant behind: `has_avif` reads the
        directory listing, so an orphaned .avif would be reported as part of the set."""
        first = _staging(tmp_path, "one", {"400.jpg": b"old", "400.avif": b"old"})
        store.publish("products/1/1", first)
        second = _staging(tmp_path, "two", {"400.jpg": b"new"})

        store.publish("products/1/1", second)

        assert store.list_dir("products/1/1") == {"400.jpg"}
        assert store.open("products/1/1/400.jpg").read() == b"new"

    def test_staging_dir_survives_for_the_caller_to_clean_up(self, store, tmp_path) -> None:
        """`_staging` rmtree's the directory in a finally block after publishing, so
        publish must not leave that path missing."""
        staging = _staging(tmp_path, "stage", {"400.jpg": b"a"})

        store.publish("products/1/1", staging)

        assert os.path.isdir(staging)


# --- put ---------------------------------------------------------------------


class TestPut:
    def test_adds_one_file_without_touching_siblings(self, store, tmp_path) -> None:
        staging = _staging(tmp_path, "stage", {"400.jpg": b"a"})
        store.publish("products/1/1", staging)

        store.put("products/1/1/og.jpg", b"og", "image/jpeg")

        assert store.list_dir("products/1/1") == {"400.jpg", "og.jpg"}

    def test_creates_the_directory_when_missing(self, store) -> None:
        store.put("products/9/9/og.jpg", b"og", "image/jpeg")

        assert store.open("products/9/9/og.jpg").read() == b"og"

    def test_overwrites_an_existing_file(self, store) -> None:
        store.put("products/1/1/og.jpg", b"first", "image/jpeg")

        store.put("products/1/1/og.jpg", b"second", "image/jpeg")

        assert store.open("products/1/1/og.jpg").read() == b"second"

    def test_leaves_no_partial_file_behind(self, store) -> None:
        """The write goes through a `.part` rename; a listing must never show it."""
        store.put("products/1/1/og.jpg", b"og", "image/jpeg")

        assert store.list_dir("products/1/1") == {"og.jpg"}


# --- list_dir / exists -------------------------------------------------------


class TestListDir:
    def test_empty_for_a_missing_directory(self, store) -> None:
        assert store.list_dir("products/nope/nope") == frozenset()

    def test_excludes_subdirectories(self, store, tmp_path) -> None:
        staging = _staging(tmp_path, "stage", {"400.jpg": b"a"})
        store.publish("products/1", staging)
        store.put("products/1/sub/400.jpg", b"a", "image/jpeg")

        assert store.list_dir("products/1") == {"400.jpg"}

    def test_exists_is_answered_from_the_listing(self, store) -> None:
        store.put("products/1/1/og.jpg", b"og", "image/jpeg")

        assert store.exists("products/1/1/og.jpg")
        assert not store.exists("products/1/1/missing.jpg")


# --- open --------------------------------------------------------------------


class TestOpen:
    def test_reads_stored_bytes(self, store) -> None:
        store.put("products/1/1/400.jpg", b"payload", "image/jpeg")

        with store.open("products/1/1/400.jpg") as fh:
            assert fh.read() == b"payload"

    def test_raises_oserror_when_missing(self, store) -> None:
        """The backfill catches OSError to skip unreadable media, so a missing file
        must not surface as some other exception type."""
        with pytest.raises(OSError):
            store.open("products/1/1/gone.jpg")


# --- delete_prefix -----------------------------------------------------------


class TestDeletePrefix:
    def test_removes_the_whole_subtree(self, store) -> None:
        store.put("products/3/1/400.jpg", b"a", "image/jpeg")
        store.put("products/3/2/400.jpg", b"a", "image/jpeg")

        store.delete_prefix("products/3")

        assert store.list_dir("products/3/1") == frozenset()
        assert store.list_dir("products/3/2") == frozenset()

    def test_leaves_siblings_alone(self, store) -> None:
        store.put("products/3/1/400.jpg", b"a", "image/jpeg")
        store.put("products/4/1/400.jpg", b"a", "image/jpeg")

        store.delete_prefix("products/3")

        assert store.list_dir("products/4/1") == {"400.jpg"}

    def test_is_safe_on_a_missing_prefix(self, store) -> None:
        store.delete_prefix("products/does-not-exist")  # must not raise


# --- url_for -----------------------------------------------------------------


class TestUrlFor:
    def test_builds_a_media_url(self, store) -> None:
        assert store.url_for("products/1/7/400.jpg") == "/media/products/1/7/400.jpg"

    @pytest.mark.parametrize("prefix", ["media", "/media", "/media/"])
    def test_normalizes_the_prefix(self, tmp_path, prefix: str) -> None:
        store = LocalMediaStore(str(tmp_path), prefix)

        assert store.url_for("a/b.jpg") == "/media/a/b.jpg"


# --- selection ---------------------------------------------------------------


class TestSelection:
    def test_defaults_to_local_outside_an_app_context(self) -> None:
        assert source() == SOURCE_LOCAL
        assert is_local()

    def test_app_is_wired_with_the_local_store_by_default(self, app) -> None:
        with app.app_context():
            store = get_store()
            assert isinstance(store, LocalMediaStore)
            assert store.root == app.config["MEDIA_ROOT"]
            assert is_local()

    def test_unknown_backend_falls_back_to_local_and_says_so(
        self, tmp_path, monkeypatch
    ) -> None:
        """A typo in MEDIA_STORE must not leave the flag and the store disagreeing:
        that combination builds a local store while `/media` refuses to serve it, so
        every product image 404s on a one-character mistake."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SEED_PRODUCTS", "0")
        monkeypatch.setenv("MEDIA_STORE", "blobb")
        from app.factory import create_app

        application = create_app()

        with application.app_context():
            assert isinstance(get_store(), LocalMediaStore)
            assert application.config["MEDIA_STORE"] == SOURCE_LOCAL
            assert is_local()
            assert source() == SOURCE_LOCAL

    def test_media_url_is_served_whenever_the_store_is_local(
        self, tmp_path, monkeypatch
    ) -> None:
        """The route guard and the built store must agree, or uploaded media 404s."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SEED_PRODUCTS", "0")
        monkeypatch.setenv("MEDIA_STORE", "blobb")
        from app.factory import create_app

        application = create_app()
        with application.app_context():
            get_store().put("probe/hello.txt", b"hi", "text/plain")

        response = application.test_client().get("/media/probe/hello.txt")

        assert response.status_code == 200
        assert response.data == b"hi"
