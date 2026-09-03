"""Vercel Blob as a MediaStore backend (see app/services/media_store.py).

Talks to the Blob HTTP API with `requests` rather than through the official `vercel`
Python SDK. The SDK is an umbrella package that pulls `cbor2` (a Rust build with no
macOS x86_64 wheel), `vercel-sandbox`, `vercel-queue`, `httpx`, `pydantic` and
`websockets` — none of which this app needs to move six files into object storage, and
which together make the adapter untestable on the dev machine. The endpoints and headers
below were read off the `@vercel/blob` 2.8.0 sources, which are what the SDKs wrap:

    PUT    https://blob.vercel-storage.com/?pathname=<p>   body = bytes
    GET    https://blob.vercel-storage.com/?prefix=&mode=folded&cursor=&limit=
    POST   https://blob.vercel-storage.com/delete          {"urls": [...]}

with `authorization: Bearer <token>` and `x-api-version: 12`.

Two things shape the implementation:

- **URLs must be derivable offline.** `Media.path` is stored in the database and every
  rendered page turns it into a URL, so `url_for` cannot make a network call. The store
  id is the fourth segment of the read-write token, and a public blob's URL is a pure
  function of (store id, pathname) — so uploads pin their pathname
  (`addRandomSuffix: 0`) instead of letting the API invent one.
- **Listings are cached.** `Media.has_avif` asks for a directory listing on every render.
  Uploaded media is immutable (a new upload is a new directory), so a short TTL is safe,
  and every write invalidates the directory it touched.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import mimetypes
import os
import time
from typing import Any, BinaryIO, Optional

import requests

from app.services.media_store import MediaStore

API_BASE = "https://blob.vercel-storage.com"
API_VERSION = "12"

# Uploaded media is immutable, so it can be cached by the CDN and browsers forever.
IMMUTABLE_MAX_AGE = 31_536_000

# The Blob service answers an occasional 503, and processing one photo is a dozen calls
# in a row, so a single blip would lose the whole upload.
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 0.5

# How long a directory listing is trusted. Only matters for media written by ANOTHER
# instance: this one invalidates on its own writes.
LISTING_TTL_SECONDS = 300

# mimetypes doesn't know these on every platform, and Blob serves back exactly the
# content type it was given — a wrong one here becomes a wrong Content-Type header.
_EXTRA_CONTENT_TYPES = {
    ".avif": "image/avif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".heic": "image/heic",
}


class BlobError(RuntimeError):
    """A Blob API call failed."""


def _store_id_from_token(token: str) -> str:
    """`vercel_blob_rw_<storeId>_<random>` -> `<storeId>`."""
    parts = token.split("_")
    if len(parts) < 4 or not parts[3]:
        raise BlobError(
            "BLOB_READ_WRITE_TOKEN no tiene el formato esperado "
            "(vercel_blob_rw_<storeId>_<random>)."
        )
    # Lowercased because it becomes a hostname. DNS resolves either case, but the API
    # returns the lowercase form, and a URL that differs from the canonical one only by
    # case is a second cache key for the same bytes — in the CDN and in every browser.
    return parts[3].lower()


def content_type_for(filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower()
    if extension in _EXTRA_CONTENT_TYPES:
        return _EXTRA_CONTENT_TYPES[extension]
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


class BlobMediaStore(MediaStore):
    def __init__(
        self,
        token: Optional[str] = None,
        *,
        session: Optional[requests.Session] = None,
        timeout: int = 30,
        listing_ttl: int = LISTING_TTL_SECONDS,
    ) -> None:
        token = token or os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
        if not token:
            raise BlobError(
                "Falta BLOB_READ_WRITE_TOKEN: MEDIA_STORE=blob no puede funcionar sin "
                "el token del store."
            )
        self._token = token
        self.store_id = _store_id_from_token(token)
        self._session = session or requests.Session()
        self._timeout = timeout
        self._listing_ttl = listing_ttl
        self._listings: dict[str, tuple[float, frozenset[str]]] = {}

    # --- HTTP -----------------------------------------------------------------

    def _headers(self, **extra: str) -> dict[str, str]:
        headers = {
            "authorization": f"Bearer {self._token}",
            "x-api-version": API_VERSION,
        }
        headers.update(extra)
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """One Blob API call, retried on the failures that are worth retrying.

        The service answers an occasional 503, and a media upload is a dozen calls in a
        row — without this, one blip in the middle of processing loses the whole upload
        and leaves a half-written directory. Retries cover 5xx, 429 and transport errors;
        a 4xx is a bug in the request and is raised immediately.
        """
        last_error: str = ""
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self._session.request(
                    method, f"{API_BASE}{path}", timeout=self._timeout, **kwargs
                )
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if response.ok:
                    if not response.content:
                        return None
                    try:
                        return response.json()
                    except ValueError:
                        return None
                last_error = f"{response.status_code}: {response.text[:300]}"
                if response.status_code < 500 and response.status_code != 429:
                    break
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        raise BlobError(f"Blob {method} {path} falló — {last_error}")

    # --- MediaStore -----------------------------------------------------------

    def url_for(self, rel_path: str) -> str:
        return f"https://{self.store_id}.public.blob.vercel-storage.com/{rel_path}"

    def put(self, rel_path: str, data: bytes, content_type: str) -> None:
        self._request(
            "PUT",
            f"/?pathname={requests.utils.quote(rel_path, safe='/')}",
            data=data,
            headers=self._headers(
                **{
                    "x-vercel-blob-access": "public",
                    "x-content-type": content_type,
                    # The pathname is the identity: Media.path in the database has to
                    # keep resolving, so the API must not append a random suffix, and a
                    # reprocess has to be allowed to replace what is there.
                    "x-add-random-suffix": "0",
                    "x-allow-overwrite": "1",
                    "x-cache-control-max-age": str(IMMUTABLE_MAX_AGE),
                }
            ),
        )
        self._invalidate(rel_path.rpartition("/")[0])

    def publish(self, rel_dir: str, staging_dir: str) -> None:
        # Replace, don't merge: a reprocess must not leave a stale variant that
        # `list_dir` would then report as part of the current set.
        self.delete_prefix(rel_dir)
        for directory, _subdirs, filenames in os.walk(staging_dir):
            for filename in filenames:
                absolute = os.path.join(directory, filename)
                relative = os.path.relpath(absolute, staging_dir).replace(os.sep, "/")
                with open(absolute, "rb") as fh:
                    data = fh.read()
                self.put(f"{rel_dir}/{relative}", data, content_type_for(filename))
        self._invalidate(rel_dir)

    def list_dir(self, rel_dir: str) -> frozenset[str]:
        cached = self._listings.get(rel_dir)
        if cached is not None and (time.monotonic() - cached[0]) < self._listing_ttl:
            return cached[1]

        prefix = f"{rel_dir}/" if rel_dir else ""
        names: set[str] = set()
        for blob in self._iter_blobs(prefix, folded=True):
            pathname = blob.get("pathname", "")
            remainder = pathname[len(prefix) :]
            # Direct children only, matching the local store's "files, not subdirs".
            if remainder and "/" not in remainder:
                names.add(remainder)

        result = frozenset(names)
        self._listings[rel_dir] = (time.monotonic(), result)
        return result

    def open(self, rel_path: str) -> BinaryIO:
        response = self._session.get(self.url_for(rel_path), timeout=self._timeout)
        if response.status_code == 404:
            # The contract the backfill relies on to skip unreadable media.
            raise FileNotFoundError(rel_path)
        if not response.ok:
            raise OSError(f"No se pudo leer {rel_path}: HTTP {response.status_code}")
        return io.BytesIO(response.content)

    def delete_prefix(self, rel_prefix: str) -> None:
        urls = [
            blob["url"]
            for blob in self._iter_blobs(f"{rel_prefix}/", folded=False)
            if blob.get("url")
        ]
        # Also the blob at exactly this pathname, if the prefix names a file.
        for blob in self._iter_blobs(rel_prefix, folded=False):
            if blob.get("pathname") == rel_prefix and blob.get("url"):
                urls.append(blob["url"])

        for batch in (urls[i : i + 100] for i in range(0, len(urls), 100)):
            self._request(
                "POST",
                "/delete",
                json={"urls": batch},
                headers=self._headers(**{"content-type": "application/json"}),
            )
        self._invalidate(rel_prefix)

    # --- browser uploads ------------------------------------------------------

    def client_upload_token(
        self,
        rel_path: str,
        *,
        allowed_content_types: list[str],
        maximum_size_in_bytes: int,
        valid_for_seconds: int = 3600,
    ) -> str:
        """A short-lived token letting ONE browser upload land at exactly `rel_path`.

        This is what gets around the 4.5 MB cap on a function's request body: the file
        never passes through the app, so a 60 MB clip is not the app's problem. The
        constraints travel inside the signed payload, so the browser cannot widen them —
        it cannot pick a different path, a bigger size or another content type.

        The scheme mirrors `@vercel/blob`'s `generateClientTokenFromReadWriteToken`:
        base64 of the JSON payload, HMAC-SHA256'd with the read-write token, then
        `vercel_blob_client_<storeId>_<base64("<hexSignature>.<payload>")>`.
        """
        payload = {
            "pathname": rel_path,
            "onUploadCompleted": None,
            "allowedContentTypes": allowed_content_types,
            "maximumSizeInBytes": maximum_size_in_bytes,
            # The upload has to land where the caller said, because the server then
            # reads that exact path back to process it.
            "addRandomSuffix": False,
            "allowOverwrite": True,
            "cacheControlMaxAge": IMMUTABLE_MAX_AGE,
            "validUntil": int((time.time() + valid_for_seconds) * 1000),
        }
        encoded = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        signature = hmac.new(
            self._token.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        inner = base64.b64encode(f"{signature}.{encoded}".encode()).decode()
        return f"vercel_blob_client_{self.store_id}_{inner}"

    @staticmethod
    def upload_url(rel_path: str) -> str:
        """Where the browser PUTs the bytes, with the client token as its bearer."""
        return f"{API_BASE}/?pathname={requests.utils.quote(rel_path, safe='/')}"

    # --- internals ------------------------------------------------------------

    def _iter_blobs(self, prefix: str, *, folded: bool):
        """Every blob under `prefix`, following the API's cursor pagination."""
        cursor = None
        while True:
            params = f"?prefix={requests.utils.quote(prefix, safe='/')}&limit=1000"
            if folded:
                params += "&mode=folded"
            if cursor:
                params += f"&cursor={requests.utils.quote(cursor, safe='')}"
            payload = self._request("GET", params, headers=self._headers()) or {}
            yield from payload.get("blobs") or []
            if not payload.get("hasMore"):
                return
            cursor = payload.get("cursor")
            if not cursor:
                return

    def _invalidate(self, rel_dir: str) -> None:
        """Drop cached listings for this directory and anything under it."""
        self._listings.pop(rel_dir, None)
        stale = [key for key in self._listings if key.startswith(f"{rel_dir}/")]
        for key in stale:
            self._listings.pop(key, None)
