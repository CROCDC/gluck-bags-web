"""Unit tests for the Tienda Nube read client (app.services.tiendanube_client).

Networking is fully mocked via an injected fake session, so these run offline and
don't need real credentials. They pin down the behaviour that's easy to get wrong:
the non-standard `Authentication` header, Link-header pagination, 429 retry with
backoff, and error surfacing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.tiendanube_client import (
    TiendaNubeClient,
    TiendaNubeError,
    _next_link,
)


# --- fakes -------------------------------------------------------------------


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        text: str = "",
        reason: str = "OK",
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text if text else ("[]" if json_data is None else "x")
        self.reason = reason

    def json(self) -> Any:
        return self._json


class FakeSession:
    """Returns queued responses in order and records every request made."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.pop(0)


def _client(responses: list[FakeResponse], **overrides: Any) -> tuple[TiendaNubeClient, FakeSession]:
    session = FakeSession(responses)
    client = TiendaNubeClient(
        store_id=overrides.get("store_id", "123"),
        access_token=overrides.get("access_token", "tok"),
        user_agent=overrides.get("user_agent", "GLUCK Test (dev@gluckbags.com)"),
        api_version=overrides.get("api_version", "2025-03"),
        max_retries=overrides.get("max_retries", 3),
        session=session,
    )
    return client, session


# --- construction / validation ----------------------------------------------


def test_requires_credentials() -> None:
    with pytest.raises(ValueError):
        TiendaNubeClient(store_id="", access_token="tok", user_agent="ua")
    with pytest.raises(ValueError):
        TiendaNubeClient(store_id="1", access_token="", user_agent="ua")
    with pytest.raises(ValueError):
        TiendaNubeClient(store_id="1", access_token="tok", user_agent="")


def test_from_env_missing_raises() -> None:
    with pytest.raises(ValueError) as exc:
        TiendaNubeClient.from_env({"TN_STORE_ID": "1"})  # no token
    assert "TN_ACCESS_TOKEN" in str(exc.value)


def test_from_env_builds_client_with_defaults() -> None:
    client = TiendaNubeClient.from_env({"TN_STORE_ID": "9", "TN_ACCESS_TOKEN": "abc"})
    assert client.store_id == "9"
    assert client.api_version == "2025-03"
    assert "@" in client.user_agent  # default UA carries a contact email


def test_base_url_shape() -> None:
    client, _ = _client([], store_id="42", api_version="2025-03")
    assert client.base_url == "https://api.tiendanube.com/2025-03/42"


# --- headers -----------------------------------------------------------------


def test_uses_nonstandard_authentication_header() -> None:
    client, session = _client([FakeResponse(json_data={"id": 1})])
    client.get_store()
    headers = session.calls[0]["headers"]
    # Tienda Nube's documented quirk: `Authentication: bearer ...`, not Authorization.
    assert headers["Authentication"] == "bearer tok"
    assert "Authorization" not in headers
    assert "@" in headers["User-Agent"]


# --- endpoints ---------------------------------------------------------------


def test_get_store_hits_store_path() -> None:
    client, session = _client([FakeResponse(json_data={"id": 7, "name": {"es": "GLÜCK"}})])
    store = client.get_store()
    assert store["id"] == 7
    assert session.calls[0]["url"] == "https://api.tiendanube.com/2025-03/123/store"


def test_list_products_passes_pagination_and_published_filter() -> None:
    client, session = _client([FakeResponse(json_data=[{"id": 1}])])
    client.list_products(page=2, per_page=25, published=True)
    params = session.calls[0]["params"]
    assert params["page"] == 2
    assert params["per_page"] == 25
    assert params["published"] == "true"


def test_get_product_builds_path() -> None:
    client, session = _client([FakeResponse(json_data={"id": 55})])
    client.get_product(55)
    assert session.calls[0]["url"].endswith("/products/55")


# --- pagination --------------------------------------------------------------


def test_iter_products_follows_link_next() -> None:
    page1 = FakeResponse(
        json_data=[{"id": 1}, {"id": 2}],
        text='[{"id":1},{"id":2}]',
        headers={
            "Link": '<https://api.tiendanube.com/2025-03/123/products?page=2>; rel="next"'
        },
    )
    page2 = FakeResponse(json_data=[{"id": 3}], text='[{"id":3}]')  # no Link -> stop
    client, session = _client([page1, page2])

    ids = [p["id"] for p in client.iter_products()]
    assert ids == [1, 2, 3]
    assert len(session.calls) == 2
    # Second call follows the absolute next URL verbatim.
    assert session.calls[1]["url"].endswith("/products?page=2")


def test_next_link_parsing() -> None:
    header = (
        '<https://x/products?page=3>; rel="next", '
        '<https://x/products?page=1>; rel="prev"'
    )
    assert _next_link(header) == "https://x/products?page=3"
    assert _next_link("") is None
    assert _next_link('<https://x>; rel="prev"') is None


# --- rate limiting & errors --------------------------------------------------


def test_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(
        "app.services.tiendanube_client.time.sleep", lambda s: slept.append(s)
    )
    throttled = FakeResponse(status_code=429, headers={"x-rate-limit-reset": "2000"})
    ok = FakeResponse(json_data={"id": 1})
    client, session = _client([throttled, ok])

    store = client.get_store()
    assert store["id"] == 1
    assert len(session.calls) == 2
    assert slept == [2.0]  # waited the reset window (2000 ms) before retrying


def test_429_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.tiendanube_client.time.sleep", lambda s: None)
    responses = [FakeResponse(status_code=429, headers={"Retry-After": "1"}) for _ in range(5)]
    client, _ = _client(responses, max_retries=2)
    with pytest.raises(TiendaNubeError) as exc:
        client.get_store()
    assert exc.value.status_code == 429


def test_4xx_raises_with_body() -> None:
    client, _ = _client(
        [FakeResponse(status_code=401, text="unauthorized", reason="Unauthorized")]
    )
    with pytest.raises(TiendaNubeError) as exc:
        client.get_store()
    assert exc.value.status_code == 401
    assert "unauthorized" in exc.value.body


# --- create_checkout ---------------------------------------------------------


def test_create_checkout_posts_draft_order_and_extracts_url() -> None:
    resp = FakeResponse(
        json_data={"id": 55, "checkout_url": "https://gluck.mitiendanube.com/checkout/55"},
        text="x",
    )
    client, session = _client([resp])
    out = client.create_checkout(
        [{"variant_id": 10, "quantity": 2}], contact={"contact_email": "ana@example.com"}
    )

    call = session.calls[0]
    assert call["method"] == "POST"
    # Redirect checkout = a draft order; its response carries checkout_url.
    assert call["url"].endswith("/draft_orders")
    body = call["json"]
    assert body["products"] == [{"variant_id": 10, "quantity": 2}]
    assert body["payment_status"] == "pending"
    # Contact fields are required by Tienda Nube to open a draft order; the email
    # is the buyer's (no placeholder fallback), the names default generic.
    assert body["contact_email"] == "ana@example.com"
    assert body["contact_name"]
    assert out["id"] == 55
    assert out["checkout_url"].startswith("https://")


def test_create_checkout_accepts_custom_contact() -> None:
    client, session = _client([FakeResponse(json_data={"id": 1, "checkout_url": "https://x/1"})])
    client.create_checkout(
        [{"variant_id": 3, "quantity": 1}],
        contact={"contact_email": "ana@example.com", "contact_name": "Ana"},
    )
    body = session.calls[0]["json"]
    assert body["contact_email"] == "ana@example.com"
    assert body["contact_name"] == "Ana"


def test_create_checkout_missing_url_returns_none() -> None:
    client, _ = _client([FakeResponse(json_data={"id": 9})])  # no url field
    out = client.create_checkout(
        [{"variant_id": 1, "quantity": 1}], contact={"contact_email": "ana@example.com"}
    )
    assert out["checkout_url"] is None


def test_create_checkout_empty_line_items_raises() -> None:
    client, _ = _client([])
    with pytest.raises(ValueError):
        client.create_checkout([], contact={"contact_email": "ana@example.com"})


def test_create_checkout_requires_buyer_email() -> None:
    """No placeholder email fallback: a draft order without the real buyer's email
    would send order confirmations to nobody."""
    client, session = _client([FakeResponse(json_data={"id": 1, "checkout_url": "https://x/1"})])
    with pytest.raises(ValueError):
        client.create_checkout([{"variant_id": 1, "quantity": 1}])
    with pytest.raises(ValueError):
        client.create_checkout([{"variant_id": 1, "quantity": 1}], contact={"contact_email": "  "})
    assert session.calls == []


# --- webhooks ----------------------------------------------------------------


def test_list_webhooks_hits_path() -> None:
    client, session = _client(
        [FakeResponse(json_data=[{"id": 1, "event": "product/updated", "url": "https://x/w"}], text="x")]
    )
    hooks = client.list_webhooks()
    assert hooks[0]["event"] == "product/updated"
    assert session.calls[0]["url"].endswith("/webhooks")


def test_create_webhook_posts_event_and_url() -> None:
    client, session = _client([FakeResponse(json_data={"id": 9, "event": "order/paid", "url": "https://x/w"})])
    out = client.create_webhook("order/paid", "https://x/w")
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/webhooks")
    assert call["json"] == {"event": "order/paid", "url": "https://x/w"}
    assert out["id"] == 9


def test_delete_webhook_hits_delete_path() -> None:
    client, session = _client([FakeResponse(json_data={}, text="x")])
    client.delete_webhook(9)
    assert session.calls[0]["method"] == "DELETE"
    assert session.calls[0]["url"].endswith("/webhooks/9")
