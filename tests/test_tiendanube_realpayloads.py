"""Coverage against the REAL Tienda Nube API shapes (captured live from store
gluck29, store_id 7949553), fully mocked so nothing hits the network or creates
draft orders.

These pin the quirks the synthetic fixtures didn't catch:
- a variant with `stock_management: false` + `stock: null` (unmanaged = unlimited),
- NO `currency` key on the variant (currency lives on the store → adapter falls to ARS),
- a price as the string "1000.00",
- `categories: []` and `description: {"es": ""}`,
- images carrying `width`/`height`,
- and the draft-order response whose `checkout_url` is the buyer redirect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models import TiendaNubeProduct
from app.services import catalog, webhook_service
from app.services.tiendanube_client import TiendaNubeClient

if TYPE_CHECKING:
    from flask import Flask


# --- captured live payloads (lightly trimmed) --------------------------------

REAL_PRODUCT: dict[str, Any] = {
    "id": 355079367,
    "name": {"es": "TEST"},
    "description": {"es": ""},
    "handle": {"es": "test-fty4d"},
    "published": True,
    "requires_shipping": True,
    "canonical_url": "https://gluck29.mitiendanube.com/productos/test-fty4d/",
    "video_url": None,
    "brand": None,
    "has_stock": True,
    "created_at": "2026-07-11T01:29:50+0000",
    "updated_at": "2026-07-11T01:30:10+0000",
    "variants": [
        {
            "id": 1557286433,
            "image_id": None,
            "product_id": 355079367,
            "position": 1,
            "price": "1000.00",
            "compare_at_price": "1000.00",
            "promotional_price": "1000.00",
            "stock_management": False,
            "stock": None,
            "sku": None,
            "values": [],
            "visible": True,
            "inventory_levels": [
                {"id": 1189027796, "variant_id": 1557286433, "location_id": "01KX7BWV61", "stock": None}
            ],
        }
    ],
    "images": [
        {
            "id": 1230623915,
            "product_id": 355079367,
            "src": "https://dcdn-us.mitiendanube.com/stores/007/949/553/products/nomeolvides-1024-1024.jpg",
            "position": 1,
            "alt": {"es": ""},
            "height": 2224,
            "width": 3024,
        }
    ],
    "videos": [],
    "categories": [],
}

# The draft-order response (buyer redirect lives in `checkout_url`, amid many keys).
REAL_DRAFT_ORDER: dict[str, Any] = {
    "id": 2016171999,
    "token": "58c3738cdef51d8b0673192f479f7118892cd725",
    "abandoned_checkout_url": "https://gluck29.mitiendanube.com/checkout/v3/abandoned/2016171999",
    "checkout_enabled": True,
    "checkout_url": "https://gluck29.mitiendanube.com/checkout/v3/start/2016171999/58c3738c?from_store=1&country=AR",
    "contact_email": "ventas@gluckbags.com",
    "contact_name": "Cliente",
    "currency": "ARS",
    "payment_status": "pending",
    "products": [{"variant_id": 1557286433, "quantity": 1, "price": "1000.00"}],
    "subtotal": "1000.00",
    "total": "1000.00",
    "store_id": 7949553,
}


# --- model mapping against the real product ----------------------------------


def test_apply_payload_maps_real_product(app: "Flask") -> None:
    with app.app_context():
        row = TiendaNubeProduct(tn_id=REAL_PRODUCT["id"]).apply_payload(REAL_PRODUCT)
        assert row.name == "TEST"
        assert row.description is None  # {"es": ""} -> empty -> None
        assert row.category is None  # categories: []
        assert row.price == "1000.00"
        assert row.currency is None  # real quirk: no currency on the variant
        assert row.stock is None  # stock_management off -> unmanaged
        assert row.in_stock is True  # None stock is NEVER "out of stock"
        assert row.published is True
        assert row.images == [
            "https://dcdn-us.mitiendanube.com/stores/007/949/553/products/nomeolvides-1024-1024.jpg"
        ]


# --- storefront adapter against the real product -----------------------------


def test_adapter_over_real_product(app: "Flask") -> None:
    app.config["CATALOG_SOURCE"] = "tiendanube"
    with app.app_context():
        from app.factory import db

        db.session.add(TiendaNubeProduct(tn_id=REAL_PRODUCT["id"]).apply_payload(REAL_PRODUCT))
        db.session.commit()

        p = catalog.get_by_id(REAL_PRODUCT["id"])
        assert p.price == 1000  # "1000.00" -> int pesos
        assert p.formatted_price == "$ 1.000"
        assert p.currency == "ARS"  # adapter falls back when the row has no currency
        assert p.in_stock is True
        assert catalog.is_purchasable(p) is True
        # Image dimensions come through from the real payload (good for CLS).
        assert p.cover.width == 3024 and p.cover.height == 2224
        assert p.cover.src.endswith("nomeolvides-1024-1024.jpg")


# --- checkout extraction against the real draft-order response ---------------


class _Resp:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.text = "x"

    def json(self) -> Any:
        return self._data


class _Session:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._resp


def test_create_checkout_extracts_url_from_real_draft_order() -> None:
    session = _Session(_Resp(REAL_DRAFT_ORDER))
    client = TiendaNubeClient(store_id="7949553", access_token="tok", user_agent="ua (x@y.z)", session=session)
    out = client.create_checkout(
        [{"variant_id": 1557286433, "quantity": 1}], contact={"contact_email": "ana@example.com"}
    )

    assert session.calls[0]["url"].endswith("/draft_orders")
    assert out["id"] == 2016171999
    assert out["checkout_url"].startswith("https://gluck29.mitiendanube.com/checkout/v3/start/")


# --- webhook upsert with the real product ------------------------------------


class _FakeClient:
    def get_product(self, product_id: int) -> dict[str, Any]:
        return REAL_PRODUCT


def test_webhook_upserts_real_product(app: "Flask") -> None:
    import hashlib
    import hmac
    import json

    secret = "s3cr3t"
    raw = json.dumps({"store_id": 7949553, "event": "product/updated", "id": REAL_PRODUCT["id"]}).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    with app.app_context():
        body, status = webhook_service.process(raw, sig, secret=secret, client=_FakeClient())
        assert status == 200 and body["action"] == "upserted"
        row = TiendaNubeProduct.query.filter_by(tn_id=REAL_PRODUCT["id"]).one()
        assert row.name == "TEST" and row.in_stock is True
