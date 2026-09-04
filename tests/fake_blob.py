"""An in-memory stand-in for the Vercel Blob HTTP API.

`BlobMediaStore` talks HTTP, so the only way to test it without a live store is to
answer that HTTP. This fake implements the endpoints the adapter uses, with the same
shapes the real API returns, and it is deliberately strict: a missing bearer token, a
missing `x-api-version` or an unknown route is an error rather than a shrug, so a
mistake in the adapter's request building surfaces here instead of on Vercel.

It paginates in small pages so the adapter's cursor handling is actually exercised —
with a single page, a broken loop would still pass.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

API_HOST = "blob.vercel-storage.com"
PAGE_SIZE = 3


class FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"", headers: dict | None = None):
        self.status_code = status_code
        self.content = body
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        return jsonlib.loads(self.content)


class FakeBlobApi:
    """Implements just enough of the Blob API to stand in for `requests.Session`."""

    def __init__(self, store_id: str = "abc123fakestore") -> None:
        self.store_id = store_id
        self.blobs: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, str]] = []

    @property
    def token(self) -> str:
        return f"vercel_blob_rw_{self.store_id}_secret"

    def public_url(self, pathname: str) -> str:
        return f"https://{self.store_id}.public.blob.vercel-storage.com/{pathname}"

    # --- requests.Session surface --------------------------------------------

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url))
        parts = urlsplit(url)
        headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}

        if parts.netloc == API_HOST:
            if "authorization" not in headers:
                return FakeResponse(401, b'{"error":{"message":"no token"}}')
            if headers.get("x-api-version") != "12":
                return FakeResponse(400, b'{"error":{"message":"bad api version"}}')

        if method == "PUT" and parts.netloc == API_HOST:
            return self._put(parts.query, kwargs.get("data") or b"", headers)
        if method == "GET" and parts.netloc == API_HOST:
            return self._list(parts.query)
        if method == "POST" and parts.path == "/delete":
            return self._delete(kwargs.get("json") or {})
        if method == "GET" and parts.netloc.endswith(".blob.vercel-storage.com"):
            return self._read(parts.path.lstrip("/"))
        return FakeResponse(404, b'{"error":{"message":"unknown route"}}')

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    # --- endpoints ------------------------------------------------------------

    def _put(self, query: str, data: bytes, headers: dict) -> FakeResponse:
        pathname = unquote((parse_qs(query).get("pathname") or [""])[0])
        if not pathname:
            return FakeResponse(400, b'{"error":{"message":"missing pathname"}}')
        if pathname in self.blobs and headers.get("x-allow-overwrite") != "1":
            return FakeResponse(409, b'{"error":{"message":"blob already exists"}}')
        if headers.get("x-add-random-suffix") == "1":
            # The adapter must pin pathnames: Media.path in the database has to keep
            # resolving to the same URL.
            pathname = f"{pathname}-random"
        self.blobs[pathname] = (data, headers.get("x-content-type", "application/octet-stream"))
        return FakeResponse(
            200,
            jsonlib.dumps(
                {
                    "url": self.public_url(pathname),
                    "downloadUrl": self.public_url(pathname) + "?download=1",
                    "pathname": pathname,
                    "contentType": self.blobs[pathname][1],
                }
            ).encode(),
        )

    def _list(self, query: str) -> FakeResponse:
        params = parse_qs(query)
        prefix = unquote((params.get("prefix") or [""])[0])
        folded = (params.get("mode") or [""])[0] == "folded"
        cursor = unquote((params.get("cursor") or [""])[0])

        matching = sorted(p for p in self.blobs if p.startswith(prefix))
        if folded:
            # The real API returns direct children as blobs and deeper paths grouped
            # into `folders`.
            matching = [p for p in matching if "/" not in p[len(prefix) :]]

        start = int(cursor) if cursor.isdigit() else 0
        page = matching[start : start + PAGE_SIZE]
        next_start = start + PAGE_SIZE
        has_more = next_start < len(matching)

        return FakeResponse(
            200,
            jsonlib.dumps(
                {
                    "blobs": [
                        {
                            "url": self.public_url(p),
                            "pathname": p,
                            "size": len(self.blobs[p][0]),
                        }
                        for p in page
                    ],
                    "cursor": str(next_start) if has_more else None,
                    "hasMore": has_more,
                    "folders": [],
                }
            ).encode(),
        )

    def _delete(self, payload: dict) -> FakeResponse:
        for url in payload.get("urls") or []:
            pathname = urlsplit(url).path.lstrip("/") if url.startswith("http") else url
            self.blobs.pop(pathname, None)
        return FakeResponse(200, b"{}")

    def _read(self, pathname: str) -> FakeResponse:
        entry = self.blobs.get(unquote(pathname))
        if entry is None:
            return FakeResponse(404, b"not found")
        return FakeResponse(200, entry[0], {"content-type": entry[1]})
