#!/usr/bin/env python3
"""Fase 0 helper: exchange a Tienda Nube OAuth `code` for a permanent access token.

Run this ONCE, after installing your app in the store (which redirects to your
redirect URI with `?code=...`). It prints the two lines to paste into `.env`.

    # after visiting https://www.tiendanube.com/apps/<APP_ID>/authorize and
    # copying the `code` from the redirect URL:
    TN_CLIENT_ID=... TN_CLIENT_SECRET=... python scripts/tn_oauth.py <code>

Tienda Nube access tokens don't expire, so you only do this once per store. The
response's `user_id` IS your store id.

Docs (blocked from this environment, kept for reference): the token endpoint is
    POST https://www.tiendanube.com/apps/authorize/token
with body {client_id, client_secret, grant_type: "authorization_code", code}.
Confirm the exact URL in your Partners panel if it differs.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

TOKEN_URL = "https://www.tiendanube.com/apps/authorize/token"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tienda Nube OAuth code → token")
    parser.add_argument("code", help="the OAuth `code` from the redirect URL")
    parser.add_argument(
        "--url", default=TOKEN_URL, help=f"token endpoint (default: {TOKEN_URL})"
    )
    args = parser.parse_args(argv)

    load_dotenv()
    client_id = os.environ.get("TN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TN_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        print(
            "✗ Faltan TN_CLIENT_ID / TN_CLIENT_SECRET (los da el panel de Partners "
            "al crear la app). Ponelos en .env o pasalos por entorno.",
            file=sys.stderr,
        )
        return 2

    import requests

    try:
        resp = requests.post(
            args.url,
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": args.code,
            },
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
    except requests.RequestException as exc:
        print(f"✗ Error de red al llamar a {args.url}: {exc}", file=sys.stderr)
        return 1

    if resp.status_code >= 400:
        print(f"✗ Tienda Nube respondió {resp.status_code}:", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        print(
            "\nCausas típicas: el `code` ya se usó o venció (pedí uno nuevo abriendo "
            "de nuevo la URL de autorización), o client_id/secret incorrectos.",
            file=sys.stderr,
        )
        return 1

    data = resp.json()
    token = data.get("access_token")
    store_id = data.get("user_id")
    if not token or not store_id:
        print("✗ Respuesta inesperada (sin access_token/user_id):", file=sys.stderr)
        print(data, file=sys.stderr)
        return 1

    print("✓ Token obtenido. Pegá estas líneas en tu .env:\n")
    print(f"TN_STORE_ID={store_id}")
    print(f"TN_ACCESS_TOKEN={token}")
    scope = data.get("scope")
    if scope:
        print(f"\n(scopes otorgados: {scope})")
    print("\nLuego validá con:  python scripts/tn_spike.py --all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
