"""Tienda Nube HTTP surface (headless POC): the webhook receiver and a small OAuth
callback helper.

- ``POST /webhooks/tiendanube`` — receives product/order notifications, verifies the
  HMAC signature and refreshes the mirror (see app/services/webhook_service.py).
- ``GET /tn/callback`` — the redirect URI used during Fase 0. Tienda Nube redirects
  the browser here with ``?code=...`` after you authorize the app in your store; this
  page just surfaces that code and the exact command to exchange it for a token. It's
  a setup convenience — it holds no secret and does nothing in production.
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, render_template, request

from app.services import webhook_service


def register_tiendanube(app: Flask) -> None:
    @app.route("/webhooks/tiendanube", methods=["POST"])
    def tiendanube_webhook() -> Response:
        # Raw bytes (not request.json): the HMAC is computed over the exact body, so
        # any re-serialization would change the digest and fail verification.
        raw = request.get_data()
        signature = request.headers.get(webhook_service.SIGNATURE_HEADER, "")
        body, status = webhook_service.process(raw, signature)
        return jsonify(body), status

    @app.route("/tn/callback", methods=["GET"])
    def tn_callback() -> str:
        # Surface the OAuth `code` from the redirect so Fase 0 doesn't require reading
        # the URL bar by hand. No code yet -> show what to do.
        return render_template("tn_callback.html", code=request.args.get("code"))
