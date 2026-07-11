"""Cart HTTP surface: a small JSON API for the drawer, a `/carrito` page and the
checkout handoff seam (headless POC, Fase 3a).

The API is intentionally tiny and stateless-looking: every mutation returns the
freshly rebuilt cart, so the frontend never has to reconcile — it just renders what
it gets back. Mutations are POST + the session cookie is SameSite=Lax, which blocks
cross-site form posts (enough CSRF protection for this public, low-stakes surface).

`/checkout` is the seam for Fase 3b: today it validates the cart and reports that the
Tienda Nube redirect isn't wired yet; later it will create the TN cart and return a
`redirect_url`. The frontend already honours `redirect_url`, so wiring it is a
one-endpoint change.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, Response, jsonify, render_template, request

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
        # Guard: only purchasable products enter the cart. Non-priced/unpublished
        # products keep the Instagram flow, so adding one is a 409 (not silent).
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
        """Post-purchase confirmation. The buyer lands here after the Tienda Nube
        checkout, so the local cart has served its purpose — clear it so a refresh or
        a new visit starts empty."""
        cart_service.clear()
        return render_template("gracias.html")

    @app.route("/checkout", methods=["POST"])
    def checkout() -> Response:
        """Checkout handoff. Delegates to checkout_service, which creates the Tienda
        Nube cart and returns a redirect_url when TN is configured, or a clear status
        (empty / integration_pending / unmapped / …) otherwise. The frontend redirects
        to `redirect_url` when `ready` is true."""
        from app.services import checkout_service

        result = checkout_service.start_checkout()
        status = 400 if result.get("reason") == "empty" else 200
        return jsonify(result), status
