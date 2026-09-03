"""Tienda Nube HTTP surface (headless POC): the webhook receiver, the mirror-sync
trigger and a small OAuth callback helper.

- ``POST /webhooks/tiendanube`` — receives product/order notifications, verifies the
  HMAC signature and refreshes the mirror (see app/services/webhook_service.py).
- ``GET /internal/sync-tn`` — runs a forced mirror sync, for a scheduler outside this
  process (see app/services/tn_scheduler.py).
- ``GET /tn/callback`` — the redirect URI used during Fase 0. Tienda Nube redirects
  the browser here with ``?code=...`` after you authorize the app in your store; this
  page just surfaces that code and the exact command to exchange it for a token. It's
  a setup convenience — it holds no secret and does nothing in production.
"""

from __future__ import annotations

import hmac
import os

from flask import Flask, Response, abort, current_app, jsonify, render_template, request

from app.services import tn_scheduler, webhook_service


def register_tiendanube(app: Flask) -> None:
    @app.route("/webhooks/tiendanube", methods=["POST"])
    def tiendanube_webhook() -> Response:
        # Raw bytes (not request.json): the HMAC is computed over the exact body, so
        # any re-serialization would change the digest and fail verification.
        raw = request.get_data()
        signature = request.headers.get(webhook_service.SIGNATURE_HEADER, "")
        body, status = webhook_service.process(raw, signature)
        return jsonify(body), status

    # GET, because the schedulers that call this only issue GETs. It mutates, so it is
    # deliberately not linked, not in the sitemap, and refuses to exist without a secret.
    @app.route("/internal/sync-tn", methods=["GET", "POST"])
    def internal_sync_tn() -> tuple[Response, int]:
        secret = os.environ.get("CRON_SECRET", "")
        if not secret:
            # 404, not 500: an unconfigured deploy must not expose an anonymous trigger
            # for a full catalogue resync, and must not advertise that the route exists.
            abort(404)
        # Compared as bytes: compare_digest raises TypeError on a non-ASCII str, and the
        # header is attacker-controlled.
        expected = f"Bearer {secret}".encode()
        provided = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(provided, expected):
            abort(401)

        # Missing credentials is a misconfiguration the caller has to SEE: returning 200
        # would keep the scheduler green while the mirror silently went stale.
        if not tn_scheduler.is_configured():
            return jsonify({"status": "not_configured"}), 503

        result = tn_scheduler.run_sync(current_app._get_current_object(), force=True)
        if result is None:
            # Another sync holds the lock, or the sync itself failed and swallowed the
            # error. Not an error for the caller: the next tick picks it up.
            return jsonify({"status": "skipped"}), 200
        return (
            jsonify(
                {
                    "status": "ok",
                    "created": result.created,
                    "updated": result.updated,
                    "pruned": result.pruned,
                }
            ),
            200,
        )

    @app.route("/tn/callback", methods=["GET"])
    def tn_callback() -> str:
        # Surface the OAuth `code` from the redirect so Fase 0 doesn't require reading
        # the URL bar by hand. No code yet -> show what to do.
        return render_template("tn_callback.html", code=request.args.get("code"))
