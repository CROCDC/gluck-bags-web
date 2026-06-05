"""Generate responsive image variants for the landing-page grids.

For each base image (given without extension) it writes downscaled WebP + JPEG
variants at the target widths, next to the originals, named `<base>-<width>.<ext>`.
Only widths smaller than the source are generated (never upscale).

Usage:
    python scripts/gen_responsive_images.py
"""

from __future__ import annotations

import os

from PIL import Image

STATIC = os.path.join(os.path.dirname(__file__), "..", "app", "static")

# Card images that are displayed small but were being served at full resolution.
BASES = [
    "img/productos/tote-cognac-01",
    "img/productos/tote-gris-interior",
    "img/productos/crossbody-rosa",
    "img/productos/crossbody-rosa-bandolera",
    "img/productos/clutch-rosa-sobre",
    "img/productos/tote-cognac-02",
    "img/video-posters/bucket-bag-reveal",
]

WIDTHS = [400, 600]

# Full-bleed hero / feature images: rendered at 100vw, so they need larger
# breakpoints than the grid cards. The Moto G Power emulated by Lighthouse
# asks for a ~721px slot (412 CSS px x ~1.75 DPR), so the small variant must
# be >= 721 or the browser rounds *up* to 1080 and downloads the full file.
# 768w covers that phone with headroom; 1080w is the source res for desktop.
HERO_BASES = [
    "img/hero/hero-tote-cognac-playa",
    "img/hero/hero-tote-gris-velero",
]

HERO_WIDTHS = [768, 1080]


def _source(base: str) -> str:
    for ext in (".webp", ".jpg", ".jpeg", ".png"):
        path = os.path.join(STATIC, base + ext)
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"No source image for {base}")


def _emit(base: str, widths: list[int]) -> None:
    src = _source(base)
    img = Image.open(src)
    for width in widths:
        if width >= img.width:
            continue
        height = round(img.height * width / img.width)
        resized = img.resize((width, height), Image.LANCZOS)
        webp_out = os.path.join(STATIC, f"{base}-{width}.webp")
        jpg_out = os.path.join(STATIC, f"{base}-{width}.jpg")
        resized.save(webp_out, "WEBP", quality=80, method=6)
        resized.convert("RGB").save(jpg_out, "JPEG", quality=82, optimize=True)
        print(f"{base}-{width}: {width}x{height}")


def main() -> None:
    for base in BASES:
        _emit(base, WIDTHS)
    for base in HERO_BASES:
        _emit(base, HERO_WIDTHS)


if __name__ == "__main__":
    main()
