"""Where processed media bytes live, behind one swap seam.

Same shape as ``app/services/catalog.py``: a single flag, ``MEDIA_STORE``, decides the
backend and every other module talks to the port instead of the filesystem.

- ``local`` (code default — dev, tests, and the Docker deploy) writes under
  ``MEDIA_ROOT`` and is served by the ``/media`` route.
- ``blob`` (Vercel) hands the bytes to an object store that serves them from its own
  CDN, because a serverless filesystem is read-only and does not survive a request.

Pillow and ffmpeg both need real paths to write to, so processing ALWAYS happens in a
local staging directory; the store only sees the finished directory, via ``publish``.
That keeps the image/video pipeline byte-identical across backends — only the last step
differs — and it means a failed transcode publishes nothing at all.

Directory listings, not per-file probes, are the read primitive (``list_dir``): the
templates ask "does this media have a full AVIF set?" on every render, which is one
listing per media instead of one probe per width. On a remote store that is the
difference between one call and four.
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from typing import BinaryIO

from flask import current_app, has_app_context

SOURCE_LOCAL = "local"
SOURCE_BLOB = "blob"

# Public URL prefix the `/media` route serves LocalMediaStore from. Uploaded files are
# immutable (a new upload = a new directory), so those URLs need no cache buster.
MEDIA_URL_PREFIX = "/media"


class MediaStore(ABC):
    """The read/write surface the app needs for processed media."""

    @abstractmethod
    def publish(self, rel_dir: str, staging_dir: str) -> None:
        """Move every file in `staging_dir` under `rel_dir`, replacing what is there."""

    @abstractmethod
    def put(self, rel_path: str, data: bytes, content_type: str) -> None:
        """Write one file, leaving the rest of its directory alone.

        `publish` replaces a whole directory, which is what processing wants; this is
        for adding a variant to media that already exists (the startup backfill).
        """

    @abstractmethod
    def list_dir(self, rel_dir: str) -> frozenset[str]:
        """The file names directly under `rel_dir`; empty when it does not exist."""

    @abstractmethod
    def open(self, rel_path: str) -> BinaryIO:
        """Open one stored file for reading. Raises OSError when it is missing."""

    @abstractmethod
    def delete_prefix(self, rel_prefix: str) -> None:
        """Delete everything under `rel_prefix`. Never raises for a missing prefix."""

    @abstractmethod
    def url_for(self, rel_path: str) -> str:
        """The public URL that serves `rel_path`."""

    def exists(self, rel_path: str) -> bool:
        """Whether one stored file is present. Prefer `list_dir` when asking about
        several files in the same directory — this is one listing per call."""
        rel_dir, _, name = rel_path.rpartition("/")
        return name in self.list_dir(rel_dir)


class LocalMediaStore(MediaStore):
    """Files on a real filesystem, served by the app's own ``/media`` route.

    This is the pre-Vercel behaviour, kept as the default so local dev, the test suite
    and the Docker deploy are unaffected by the port existing.
    """

    def __init__(self, root: str, url_prefix: str) -> None:
        self.root = root
        # A URL path, joined with "/" — never os.path.join, which emits backslashes
        # on Windows and 404s.
        self.url_prefix = "/" + url_prefix.strip("/")

    def _abs(self, rel_path: str) -> str:
        return os.path.join(self.root, rel_path)

    def publish(self, rel_dir: str, staging_dir: str) -> None:
        dest = self._abs(rel_dir)
        # Replace rather than merge, so a reprocess can't leave a stale variant behind
        # that `list_dir` would then report as part of the current set.
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(os.path.dirname(dest) or self.root, exist_ok=True)
        shutil.move(staging_dir, dest)
        # `publish` owns the staging dir once it is moved; recreate it so the caller's
        # cleanup stays a no-op instead of an error.
        os.makedirs(staging_dir, exist_ok=True)

    def put(self, rel_path: str, data: bytes, content_type: str) -> None:
        path = self._abs(rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write-then-rename: a concurrent reader never sees a half-written variant.
        tmp = f"{path}.part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def list_dir(self, rel_dir: str) -> frozenset[str]:
        try:
            with os.scandir(self._abs(rel_dir)) as entries:
                return frozenset(entry.name for entry in entries if entry.is_file())
        except OSError:
            return frozenset()

    def open(self, rel_path: str) -> BinaryIO:
        return open(self._abs(rel_path), "rb")

    def delete_prefix(self, rel_prefix: str) -> None:
        shutil.rmtree(self._abs(rel_prefix), ignore_errors=True)

    def url_for(self, rel_path: str) -> str:
        return f"{self.url_prefix}/{rel_path}"


def source() -> str:
    if not has_app_context():
        return SOURCE_LOCAL
    return (current_app.config.get("MEDIA_STORE") or SOURCE_LOCAL).strip().lower()


def is_local() -> bool:
    return source() == SOURCE_LOCAL


def build_store(app: object) -> MediaStore:
    """The store `app` is configured for. Called once from the app factory.

    Normalizes MEDIA_STORE back into the config, so the flag can never disagree with
    the store that was actually built: a typo'd value falls back to local AND reports
    itself as local, instead of leaving the `/media` route refusing to serve the very
    files the local store is writing.
    """
    config = app.config  # type: ignore[attr-defined]
    name = (config.get("MEDIA_STORE") or SOURCE_LOCAL).strip().lower()
    if name not in (SOURCE_LOCAL, SOURCE_BLOB):
        app.logger.warning(  # type: ignore[attr-defined]
            "MEDIA_STORE=%r no es un backend conocido; se usa %r", name, SOURCE_LOCAL
        )
        name = SOURCE_LOCAL
    config["MEDIA_STORE"] = name

    if name == SOURCE_BLOB:
        # Imported lazily: the Vercel SDK is installed only on Vercel (its cbor2
        # dependency has no macOS x86_64 wheel), so a local run must never import it.
        from app.services.media_store_blob import BlobMediaStore

        return BlobMediaStore()
    return LocalMediaStore(config["MEDIA_ROOT"], MEDIA_URL_PREFIX)


def get_store() -> MediaStore:
    """The current app's store. Requires an app context."""
    return current_app.extensions["media_store"]
