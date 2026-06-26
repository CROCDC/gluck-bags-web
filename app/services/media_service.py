"""Media processing: turn an uploaded photo/video into web-optimized files.

Images -> responsive WebP + JPEG variants (Pillow).
Videos -> a compressed, web-friendly MP4 + a poster frame (ffmpeg).

Everything is written under MEDIA_ROOT/<rel_dir>/, where rel_dir is
`products/<product_id>/<media_id>`. The public site serves these via the
`media_file` route.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from flask import current_app
from PIL import Image, ImageOps
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# Responsive widths generated for each image. The grid uses 400/600; the product
# detail page can use up to 1000. Originals are capped at MAX_IMAGE_DIM.
IMAGE_WIDTHS = [400, 600, 1000]
MAX_IMAGE_DIM = 1600

# Videos are scaled so the longest side is at most this, to keep them light.
VIDEO_MAX_DIM = 1280

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"}
VIDEO_EXTS = {"mp4", "mov", "m4v", "webm", "avi", "mkv", "3gp", "ogv", "mpg", "mpeg"}


class MediaError(Exception):
    """Raised when an upload can't be processed (bad file, ffmpeg failure, …)."""


def ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def classify(filename: str) -> str | None:
    """Return 'image', 'video', or None if the extension is unsupported."""
    ext = ext_of(filename)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _media_root() -> str:
    return current_app.config["MEDIA_ROOT"]


def _rel_dir(product_id: int, media_id: int) -> str:
    return f"products/{product_id}/{media_id}"


def _abs_dir(rel_dir: str) -> str:
    return os.path.join(_media_root(), rel_dir)


# --- Images ------------------------------------------------------------------


def process_image(source: FileStorage | str, product_id: int, media_id: int) -> dict:
    """Generate responsive WebP+JPEG variants. Returns dict for the Media row."""
    rel_dir = _rel_dir(product_id, media_id)
    abs_dir = _abs_dir(rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    try:
        img = Image.open(source.stream if isinstance(source, FileStorage) else source)
        # Respect EXIF orientation (phone photos) then drop the metadata.
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB",):
            img = img.convert("RGB")
    except Exception as exc:  # noqa: BLE001 — surface a friendly error
        shutil.rmtree(abs_dir, ignore_errors=True)
        raise MediaError("No pudimos leer la imagen. Probá con un JPG o PNG.") from exc

    # Cap the original so we never store/serve anything huge.
    if max(img.width, img.height) > MAX_IMAGE_DIM:
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)

    widths = sorted({w for w in IMAGE_WIDTHS if w < img.width} | {img.width})
    for width in widths:
        if width == img.width:
            resized = img
        else:
            height = round(img.height * width / img.width)
            resized = img.resize((width, height), Image.LANCZOS)
        resized.save(os.path.join(abs_dir, f"{width}.webp"), "WEBP", quality=80, method=6)
        resized.save(os.path.join(abs_dir, f"{width}.jpg"), "JPEG", quality=82, optimize=True)

    return {
        "kind": "image",
        "path": rel_dir,
        "width": img.width,
        "height": img.height,
        "widths": widths,
    }


# --- Videos ------------------------------------------------------------------


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"No pudimos procesar el video: {proc.stderr.strip()[:300]}")


def process_video(source: FileStorage | str, product_id: int, media_id: int) -> dict:
    """Transcode to a compressed web MP4 + extract a poster. Returns Media dict."""
    rel_dir = _rel_dir(product_id, media_id)
    abs_dir = _abs_dir(rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    # ffmpeg needs a real input path; stream the upload to a temp file first.
    tmp_path: str | None = None
    if isinstance(source, FileStorage):
        suffix = "." + (ext_of(secure_filename(source.filename or "")) or "mp4")
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        source.save(tmp_path)
        input_path = tmp_path
    else:
        input_path = source

    out_mp4 = os.path.join(abs_dir, "video.mp4")
    poster = os.path.join(abs_dir, "poster.jpg")

    # Scale longest side down to VIDEO_MAX_DIM (never upscale), keep even dims for
    # yuv420p, drop the audio track (players are muted), faststart for streaming.
    scale = (
        f"scale='min({VIDEO_MAX_DIM},iw)':'min({VIDEO_MAX_DIM},ih)':"
        "force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    try:
        _run_ffmpeg([
            "-i", input_path,
            "-vf", scale,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            out_mp4,
        ])
        _run_ffmpeg(["-i", out_mp4, "-frames:v", "1", "-q:v", "3", poster])
    except MediaError:
        shutil.rmtree(abs_dir, ignore_errors=True)
        raise
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    # Read the encoded dimensions off the poster (matches the video exactly).
    try:
        with Image.open(poster) as pimg:
            width, height = pimg.width, pimg.height
    except Exception:  # noqa: BLE001
        width = height = None

    return {"kind": "video", "path": rel_dir, "width": width, "height": height, "widths": None}


# --- Cleanup -----------------------------------------------------------------


def delete_media_files(rel_dir: str) -> None:
    shutil.rmtree(_abs_dir(rel_dir), ignore_errors=True)


def delete_product_files(product_id: int) -> None:
    shutil.rmtree(os.path.join(_media_root(), "products", str(product_id)), ignore_errors=True)
