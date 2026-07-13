"""Cart HTTP surface: a small JSON API for the drawer, a `/carrito` page and the
Tienda Nube checkout handoff (`POST /checkout`), live in production.

The API is intentionally tiny and stateless-looking: every mutation returns the
freshly rebuilt cart, so the frontend never has to reconcile — it just renders what
it gets back. Mutations are POST + the session cookie is SameSite=Lax, which blocks
cross-site form posts (enough CSRF protection for this public, low-stakes surface).

`/checkout` creates a TN draft order (with the buyer's email) and returns its
`redirect_url`; the frontend redirects there and the purchase finishes on TN's
hosted checkout. Cart reads reconcile a pending handoff first, because TN offers no
return URL — see checkout_service.reconcile_pending.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, Response, jsonify, render_template, request

# checkout_service is imported lazily inside the views: it pulls the Tienda Nube
# client chain, which must not load at boot (see the factory's TN guard — an
# import-time TN failure must degrade checkout only, never 502 the site).
from app.services import cart_service


def _payload() -> dict[str, Any]:
    """Request body as a dict, accepting either JSON or form-encoded input."""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict()


def _product_id(data: dict[str, Any]) -> int | None:
    try:
        return int(data.get("product_id"))
    except (TypeError, ValueError):
        return None


def register_cart(app: Flask) -> None:
    @app.route("/api/cart", methods=["GET"])
    def api_cart() -> Response:
        return jsonify(cart_service.build())

    @app.route("/api/cart/add", methods=["POST"])
    def api_cart_add() -> Response:
        data = _payload()
        pid = _product_id(data)
        if pid is None:
            return jsonify({"error": "product_id requerido"}), 400
        try:
            qty = int(data.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1
        # Guard: only purchasable products (published + priced + in stock) enter the
        # cart; the rest 409 so the PDP's "Consultar por Instagram" fallback is the
        # only path left for them — never a silent add.
        # Looks up through the active catalogue source so a Tienda Nube id resolves.
        from app.services import catalog

        product = catalog.get_by_id(pid)
        if not cart_service.is_purchasable(product):
            return jsonify({"error": "Producto no disponible para compra online"}), 409
        cart_service.add(pid, qty)
        return jsonify(cart_service.build())

    @app.route("/api/cart/update", methods=["POST"])
    def api_cart_update() -> Response:
        data = _payload()
        pid = _product_id(data)
        if pid is None:
            return jsonify({"error": "product_id requerido"}), 400
        try:
            qty = int(data.get("qty"))
        except (TypeError, ValueError):
            return jsonify({"error": "qty inválida"}), 400
        cart_service.set_qty(pid, qty)
        return jsonify(cart_service.build())

    @app.route("/api/cart/remove", methods=["POST"])
    def api_cart_remove() -> Response:
        data = _payload()
        pid = _product_id(data)
        if pid is None:
            return jsonify({"error": "product_id requerido"}), 400
        cart_service.remove(pid)
        return jsonify(cart_service.build())

    @app.route("/api/cart/clear", methods=["POST"])
    def api_cart_clear() -> Response:
        cart_service.clear()
        return jsonify(cart_service.build())

    @app.route("/carrito", methods=["GET"])
    def cart_page() -> str:
        return render_template("cart.html", cart=cart_service.build())

    @app.route("/gracias", methods=["GET"])
    def checkout_thanks() -> str:
        """Post-purchase confirmation. TN's checkout has no automatic redirect back
        here, so anyone can also open the URL directly: only a session with a
        pending TN handoff gets its purchased lines dropped (and the conversion
        event fired) — an ordinary visitor's in-progress cart is never wiped."""
        from app.services import checkout_service

        confirmed = checkout_service.has_pending()
        if confirmed:
            checkout_service.consume_pending_purchase()
        return render_template("gracias.html", confirmed=confirmed)

    @app.route("/checkout", methods=["POST"])
    def checkout() -> Response:
        """Checkout handoff. Delegates to checkout_service, which creates the Tienda
        Nube draft order (with the buyer's email as contact) and returns a
        redirect_url, or a clear status (empty / email_required / not_configured /
        unmapped / …) otherwise. The frontend redirects when `ready` is true."""
        from app.services import checkout_service

        result = checkout_service.start_checkout(contact_email=_payload().get("email"))
        if result.get("ready"):
            checkout_service.remember_pending(result.get("checkout_id"), cart_service.raw_items())
        status = 400 if result.get("reason") in ("empty", "email_required") else 200
        return jsonify(result), status
