#!/usr/bin/env python3
"""Fase 1 spike: read the store's catalogue through the Tienda Nube API.

Run this once you've created the Partner app, installed it in the store and
obtained an access token + store id (put them in .env — see .env.example). It
verifies auth, prints the store name and a summary of products/variants/stock, so
we confirm the data shape and rate limits BEFORE wiring any of it into the UI.

    python scripts/tn_spike.py            # summary of the first products
    python scripts/tn_spike.py --all      # walk the whole catalogue (auto-paginate)

Reads no Flask app — just the environment, so it's safe to run standalone.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow `python scripts/tn_spike.py` from the repo root: put the project root (this
# file's parent's parent) on sys.path so `import app...` resolves without install.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from app.services.tiendanube_client import TiendaNubeClient, TiendaNubeError


def _name(value: object) -> str:
    """Tienda Nube localizes names as {"es": "...", "pt": "..."}; pick something."""
    if isinstance(value, dict):
        return value.get("es") or next(iter(value.values()), "") if value else ""
    return str(value or "")


def _summarize_product(product: dict) -> str:
    variants = product.get("variants") or []
    stock = sum((v.get("stock") or 0) for v in variants if v.get("stock") is not None)
    prices = [v.get("price") for v in variants if v.get("price")]
    price = f"${prices[0]}" if prices else "sin precio"
    published = "público" if product.get("published") else "oculto"
    return (
        f"  #{product.get('id')}  {_name(product.get('name')):40.40}  "
        f"{len(variants)} var  stock={stock:<4}  {price:<12}  {published}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tienda Nube read spike")
    parser.add_argument(
        "--all", action="store_true", help="walk the whole catalogue (all pages)"
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="max products to show without --all"
    )
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        client = TiendaNubeClient.from_env()
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    try:
        store = client.get_store()
        print(f"✓ Autenticado. Tienda: {_name(store.get('name'))}  (id={store.get('id')})")
        print(f"  URL: {store.get('original_domain') or store.get('url')}")

        print("\nCategorías:")
        for cat in client.list_categories():
            print(f"  #{cat.get('id')}  {_name(cat.get('name'))}")

        print("\nProductos:")
        count = 0
        source = (
            client.iter_products()
            if args.all
            else iter(client.list_products(per_page=args.limit))
        )
        for product in source:
            print(_summarize_product(product))
            count += 1
        print(f"\nTotal mostrado: {count}")
    except TiendaNubeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        if exc.body:
            print(exc.body[:500], file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
