"""Minimal read client for the Tienda Nube (Nuvemshop) REST API.

This is the first brick of the headless POC (see docs/TIENDANUBE-HEADLESS-POC.md,
"Fase 1 — Spike de lectura"). Tienda Nube has **no public storefront API**: every
call is OAuth server-to-server against the admin REST API, so this client lives in
the backend (our Flask BFF) and holds the store's access token. The browser never
talks to Tienda Nube directly.

Scope for now: reads only (store, products, categories) — enough to validate auth,
data shape and rate limits. Cart creation + the redirect-checkout handoff come in a
later phase and will extend this same client.

Docs (blocked from this environment's network, kept here for reference):
- Base URL:  https://api.tiendanube.com/{version}/{store_id}
- Auth:      header `Authentication: bearer <access_token>`  (note the non-standard
             header name — Tienda Nube does NOT use `Authorization`).
- User-Agent: REQUIRED. Tienda Nube rejects requests without an identifying UA that
             includes a contact email, e.g. "GLUCK Headless POC (dev@gluckbags.com)".
- Pagination: `page`/`per_page` query params; the next page URL comes in the `Link`
             response header with rel="next".
- Rate limit: leaky bucket. Headers `x-rate-limit-limit/remaining/reset` (reset in
             ms). On HTTP 429 we wait for the reset window and retry.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Iterator

import requests

DEFAULT_API_VERSION = "2025-03"
DEFAULT_TIMEOUT = 15  # seconds
DEFAULT_MAX_RETRIES = 3
# Tienda Nube caps per_page; 50 is a safe, polite page size for a small catalogue.
DEFAULT_PER_PAGE = 50

# Parses one entry of an RFC-5988 Link header: <url>; rel="next"
_LINK_RE = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"')


class TiendaNubeError(RuntimeError):
    """A non-retryable error from the Tienda Nube API (4xx/5xx that isn't a 429).

    Carries the HTTP status and the raw response body so callers (and the spike
    script) can show something actionable instead of a bare stack trace.
    """

    def __init__(self, status_code: int, message: str, body: str = "") -> None:
        super().__init__(f"Tienda Nube API error {status_code}: {message}")
        self.status_code = status_code
        self.body = body


class TiendaNubeClient:
    """Thin, dependency-light wrapper over the Tienda Nube admin REST API.

    Construct explicitly, or use `TiendaNubeClient.from_env()` to read credentials
    from the environment. Networking goes through a single `_request` seam so tests
    can inject a fake `session` and exercise pagination/rate-limit logic offline.
    """

    def __init__(
        self,
        store_id: str | int,
        access_token: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        user_agent: str,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        session: requests.Session | None = None,
    ) -> None:
        if not store_id or not access_token:
            raise ValueError("store_id and access_token are required")
        if not user_agent:
            raise ValueError(
                "user_agent is required — Tienda Nube rejects requests without an "
                "identifying User-Agent that includes a contact email"
            )
        self.store_id = str(store_id)
        self.access_token = access_token
        self.api_version = api_version
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_url = f"https://api.tiendanube.com/{api_version}/{self.store_id}"
        self._session = session or requests.Session()

    # --- construction --------------------------------------------------------

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "TiendaNubeClient":
        """Build a client from environment variables.

        Reads TN_STORE_ID, TN_ACCESS_TOKEN (required) and TN_API_VERSION,
        TN_USER_AGENT (optional, with sensible defaults). Raises ValueError with a
        clear message if a required variable is missing, so `scripts/tn_spike.py`
        fails loudly rather than making an unauthenticated call.
        """
        env = env if env is not None else dict(os.environ)
        store_id = env.get("TN_STORE_ID", "").strip()
        access_token = env.get("TN_ACCESS_TOKEN", "").strip()
        missing = [
            name
            for name, val in (("TN_STORE_ID", store_id), ("TN_ACCESS_TOKEN", access_token))
            if not val
        ]
        if missing:
            raise ValueError(
                "Missing Tienda Nube credentials: "
                + ", ".join(missing)
                + ". Set them in .env (see .env.example) after installing the app in "
                "the store and completing OAuth."
            )
        return cls(
            store_id=store_id,
            access_token=access_token,
            api_version=env.get("TN_API_VERSION", "").strip() or DEFAULT_API_VERSION,
            user_agent=env.get("TN_USER_AGENT", "").strip()
            or "GLUCK Headless POC (dev@gluckbags.com)",
        )

    # --- low-level HTTP ------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        # `Authentication` (not `Authorization`) is intentional — it's Tienda Nube's
        # documented, non-standard auth header.
        return {
            "Authentication": f"bearer {self.access_token}",
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> requests.Response:
        """Perform one API request, honouring rate limits.

        `path_or_url` may be a path relative to the store base (e.g. "products") or
        a full URL (used when following a `Link: rel="next"` header, which already
        carries the query string). `json` is sent as the request body for writes. On
        HTTP 429 we wait for the reset window (from `x-rate-limit-reset`, in ms) and
        retry up to `max_retries` times. Other 4xx/5xx raise TiendaNubeError.
        """
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{self.base_url}/{path_or_url.lstrip('/')}"
        )
        attempt = 0
        while True:
            attempt += 1
            response = self._session.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json,
                timeout=self.timeout,
            )
            if response.status_code == 429 and attempt <= self.max_retries:
                time.sleep(self._retry_after_seconds(response))
                continue
            if response.status_code >= 400:
                raise TiendaNubeError(
                    response.status_code,
                    response.reason or "request failed",
                    body=_safe_text(response),
                )
            return response

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float:
        """How long to wait after a 429. Prefers `x-rate-limit-reset` (ms), falls
        back to a standard `Retry-After` (s), then to a 1s floor."""
        reset_ms = response.headers.get("x-rate-limit-reset")
        if reset_ms:
            try:
                return max(0.0, float(reset_ms) / 1000.0)
            except ValueError:
                pass
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return 1.0

    # --- pagination ----------------------------------------------------------

    def _paginate(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every item across all pages, following the `Link` rel="next" header.

        Tienda Nube returns a JSON array per page and the next page URL in the Link
        header, so we don't have to guess when to stop — we follow next until it's
        gone. An empty body (204/empty array) ends iteration.
        """
        next_url: str | None = path
        first = True
        while next_url:
            response = self._request("GET", next_url, params=params if first else None)
            first = False
            page = response.json() if _safe_text(response).strip() else []
            for item in page:
                yield item
            next_url = _next_link(response.headers.get("Link", ""))

    # --- public read endpoints ----------------------------------------------

    def get_store(self) -> dict[str, Any]:
        """Store info — the cheapest authenticated call; ideal to verify the token."""
        return self._request("GET", "store").json()

    def list_products(
        self,
        *,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
        published: bool | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """One page of products. Pass `published=True` to fetch only visible ones."""
        params: dict[str, Any] = {"page": page, "per_page": per_page, **filters}
        if published is not None:
            params["published"] = "true" if published else "false"
        return self._request("GET", "products", params=params).json()

    def iter_products(
        self, *, per_page: int = DEFAULT_PER_PAGE, published: bool | None = None
    ) -> Iterator[dict[str, Any]]:
        """Every product across all pages (auto-pagination)."""
        params: dict[str, Any] = {"per_page": per_page}
        if published is not None:
            params["published"] = "true" if published else "false"
        yield from self._paginate("products", params=params)

    def get_product(self, product_id: int | str) -> dict[str, Any]:
        return self._request("GET", f"products/{product_id}").json()

    def list_categories(
        self, *, per_page: int = DEFAULT_PER_PAGE
    ) -> list[dict[str, Any]]:
        return list(self._paginate("categories", params={"per_page": per_page}))

    # --- checkout handoff (write) --------------------------------------------

    def create_checkout(
        self,
        line_items: list[dict[str, Any]],
        *,
        contact: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a draft order in Tienda Nube and return its checkout redirect URL.

        `line_items` is ``[{"variant_id": int, "quantity": int}, ...]``. Returns
        ``{"id", "checkout_url", "raw"}``; `checkout_url` is where the buyer is
        redirected to finish the purchase (payment/shipping/AFIP all handled by TN).

        Confirmed against the live API: the redirect checkout is a **draft order**
        (``POST /draft_orders``, scope ``write_draft_orders``); its response carries
        ``checkout_url``. Tienda Nube requires contact fields to open a draft order —
        the real buyer completes/edits them at the hosted checkout — so we send a
        generic placeholder (overridable via `contact`).
        """
        if not line_items:
            raise ValueError("line_items required")
        body: dict[str, Any] = {
            **_CHECKOUT_CONTACT,
            **(contact or {}),
            "payment_status": "pending",
            "products": [
                {"variant_id": int(li["variant_id"]), "quantity": int(li["quantity"])}
                for li in line_items
            ],
        }
        data = self._request("POST", "draft_orders", json=body).json()
        return {
            "id": data.get("id") if isinstance(data, dict) else None,
            "checkout_url": _extract_checkout_url(data),
            "raw": data,
        }

    # --- webhooks (write) ----------------------------------------------------

    def list_webhooks(self) -> list[dict[str, Any]]:
        """Every webhook registered for this store (auto-paginated)."""
        return list(self._paginate("webhooks"))

    def create_webhook(self, event: str, url: str) -> dict[str, Any]:
        """Register a webhook: Tienda Nube will POST `event` notifications to `url`
        (signed with the app secret; verified in webhook_service). Returns the created
        webhook (with its `id`)."""
        return self._request("POST", "webhooks", json={"event": event, "url": url}).json()

    def delete_webhook(self, webhook_id: int | str) -> None:
        self._request("DELETE", f"webhooks/{webhook_id}")


# --- module helpers ----------------------------------------------------------


def _next_link(link_header: str) -> str | None:
    """Extract the rel="next" URL from an RFC-5988 Link header, or None."""
    for match in _LINK_RE.finditer(link_header or ""):
        if match.group("rel") == "next":
            return match.group("url")
    return None


# Placeholder buyer identity for the draft order. Tienda Nube requires contact
# fields to open a draft order; the real buyer fills/confirms them at the hosted
# checkout. Kept generic and clearly ours so a stray draft order is easy to spot.
_CHECKOUT_CONTACT = {
    "contact_name": "Cliente",
    "contact_lastname": "Web",
    "contact_email": "ventas@gluckbags.com",
}

# The redirect URL lives in `checkout_url` on the draft-order response (confirmed
# against the live API). The extra keys are harmless fallbacks.
_CHECKOUT_URL_KEYS = ("checkout_url", "url", "permalink", "link", "checkout")


def _extract_checkout_url(data: Any) -> str | None:
    """Best-effort pull of the buyer redirect URL from a checkout/cart payload."""
    if not isinstance(data, dict):
        return None
    for key in _CHECKOUT_URL_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None


def _safe_text(response: requests.Response) -> str:
    """response.text without raising on odd encodings (used for error bodies)."""
    try:
        return response.text
    except Exception:  # noqa: BLE001 — never let error-reporting mask the real error
        return ""
