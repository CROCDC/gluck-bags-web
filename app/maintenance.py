"""Idempotent media self-heal, run once at startup (under the schema-init lock).

Images processed on an older build — e.g. the products seeded before the og.jpg
(1200x630 social crop) and AVIF variants existed — lack those files. This
regenerates them from the largest JPEG the media store already holds, so a deploy
heals existing media automatically, with NO manual step on the server.

Idempotent (skips variants that already exist) and defensive (a per-file error is
counted and skipped, never raised), so it's cheap on every boot and safe to run
behind gunicorn workers. AVIF is best-effort: skipped when Pillow has no encoder.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps


def _avif_supported() -> bool:
    """Probe once whether this Pillow build can encode AVIF (so a per-file failure
    isn't misread as a missing encoder)."""
    try:
        Image.new("RGB", (4, 4)).save(io.BytesIO(), "AVIF")
        return True
    except Exception:  # noqa: BLE001
        return False


def _largest_jpg(names: frozenset[str], widths: list[int]) -> str | None:
    """The widest `<w>.jpg` actually present, to re-derive variants from."""
    for w in sorted(widths or [], reverse=True):
        if f"{w}.jpg" in names:
            return f"{w}.jpg"
    return None


def _encode(data: bytes, fmt: str, **save_kwargs: object) -> bytes:
    buf = io.BytesIO()
    with Image.open(io.BytesIO(data)) as im:
        rgb = im.convert("RGB")
        if fmt == "og":
            from app.services.media_service import OG_SIZE

            ImageOps.fit(rgb, OG_SIZE, Image.LANCZOS).save(buf, "JPEG", **save_kwargs)
        else:
            rgb.save(buf, fmt, **save_kwargs)
    return buf.getvalue()


def backfill_media_variants() -> dict[str, int]:
    """Within an app context: regenerate missing og.jpg + AVIF variants for every
    image Media. Returns counts. Must be called inside `app.app_context()`."""
    from app.models import Media
    from app.services.media_store import get_store

    store = get_store()
    made_og = made_avif = failed = 0
    avif_supported = _avif_supported()

    for media in Media.query.all():
        if not media.is_image:
            continue
        widths = media.widths or []
        names = store.list_dir(media.path)
        source_name = _largest_jpg(names, widths)
        if source_name is None:
            continue

        try:
            with store.open(f"{media.path}/{source_name}") as fh:
                source_bytes = fh.read()
        except OSError:
            continue

        if "og.jpg" not in names:
            try:
                store.put(
                    f"{media.path}/og.jpg",
                    _encode(source_bytes, "og", quality=82, optimize=True),
                    "image/jpeg",
                )
                made_og += 1
            except Exception:  # noqa: BLE001 — skip this file, keep healing the rest
                failed += 1

        if not avif_supported:
            continue

        # Best-effort per width, deliberately NOT all-or-nothing: `Media.has_avif`
        # already refuses to emit AVIF sources unless every width is present, so a
        # partial set on disk is inert, and healing what we can means the next boot
        # completes the set once the unreadable source is replaced.
        for w in widths:
            if f"{w}.avif" in names or f"{w}.jpg" not in names:
                continue
            try:
                with store.open(f"{media.path}/{w}.jpg") as fh:
                    data = _encode(fh.read(), "AVIF", quality=60)
                store.put(f"{media.path}/{w}.avif", data, "image/avif")
                made_avif += 1
            except Exception:  # noqa: BLE001
                failed += 1

    return {"og": made_og, "avif": made_avif, "failed": failed}
