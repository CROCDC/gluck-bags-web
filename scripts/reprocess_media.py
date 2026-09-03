"""Manual one-off media backfill (og.jpg 1200x630 + AVIF variants).

Normally NOT needed: the app self-heals existing media at startup — see
`app.maintenance.backfill_media_variants`, wired into `create_app`'s schema init,
so every deploy regenerates missing variants with no manual step on the server.
This stays as an explicit CLI for local/ad-hoc runs.

Usage:
    python scripts/reprocess_media.py
"""

from __future__ import annotations

import os
import sys

# Make `app` importable when run as `python scripts/reprocess_media.py`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.factory import create_app  # noqa: E402
from app.maintenance import backfill_media_variants  # noqa: E402


def main() -> int:
    app = create_app()  # startup already heals; this re-runs the (idempotent) backfill
    with app.app_context():
        counts = backfill_media_variants()
    print(
        f"Backfilled og.jpg: {counts['og']} | AVIF variants: {counts['avif']}"
        f" | failed: {counts['failed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
