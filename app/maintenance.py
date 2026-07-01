"""Idempotent media self-heal, run once at startup (under the schema-init lock).

Images processed on an older build — e.g. the products seeded before the og.jpg
(1200x630 social crop) and AVIF variants existed — lack those files. This
regenerates them in place from the largest JPEG already on disk, so a deploy heals
existing media automatically, with NO manual step on the server.

Idempotent (skips files that already exist) and defensive (a per-file error is
counted and skipped, never raised), so it's cheap on every boot and safe to run
behind gunicorn workers. AVIF is best-effort: skipped when Pillow has no encoder.
"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageOps


def _avif_supported() -> bool:
    """Probe once whether this Pillow build can encode AVIF (so a per-file failure
    isn't misread as a missing encoder)."""
    try:
        Image.new("RGB", (4, 4)).save(io.BytesIO(), "AVIF")
        return True
    except Exception:  # noqa: BLE001
        return False


def _largest_jpg(abs_dir: str, widths: list[int]) -> str | None:
    for w in sorted(widths or [], reverse=True):
        path = os.path.join(abs_dir, f"{w}.jpg")
        if os.path.exists(path):
            return path
    return None


def backfill_media_variants(media_root: str) -> dict[str, int]:
    """Within an app context: regenerate missing og.jpg + AVIF variants for every
    image Media. Returns counts. Must be called inside `app.app_context()`."""
    from app.models import Media
    from app.services.media_service import OG_SIZE

    made_og = made_avif = failed = 0
    avif_supported = _avif_supported()

    for media in Media.query.all():
        if not media.is_image:
            continue
        abs_dir = os.path.join(media_root, media.path)
        source = _largest_jpg(abs_dir, media.widths or [])
        if not source:
            continue

        og_path = os.path.join(abs_dir, "og.jpg")
        if not os.path.exists(og_path):
            try:
                with Image.open(source) as im:
                    ImageOps.fit(im.convert("RGB"), OG_SIZE, Image.LANCZOS).save(
                        og_path, "JPEG", quality=82, optimize=True
                    )
                made_og += 1
            except Exception:  # noqa: BLE001 — skip this file, keep healing the rest
                failed += 1

        if not avif_supported:
            continue
        for w in media.widths or []:
            jpg = os.path.join(abs_dir, f"{w}.jpg")
            avif = os.path.join(abs_dir, f"{w}.avif")
            if not os.path.exists(jpg) or os.path.exists(avif):
                continue
            try:
                with Image.open(jpg) as im:
                    im.convert("RGB").save(avif, "AVIF", quality=60)
                made_avif += 1
            except Exception:  # noqa: BLE001
                failed += 1

    return {"og": made_og, "avif": made_avif, "failed": failed}
