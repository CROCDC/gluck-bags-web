"""Tests for the real media pipeline (Pillow + ffmpeg) in app.services.media_service.

These exercise the actual image/video processing with real generated source files
inside an app context. They verify:
  - ext_of / classify extension handling (case, unknown, no-extension)
  - process_image: variant generation, returned dict, capping, non-RGB & EXIF
    handling, and MediaError on a corrupt file (with dir cleanup)
  - process_video: produces video.mp4 + poster.jpg, scales down >1280 inputs to an
    even-dimensioned longest-side <= 1280, strips audio, returns width/height
  - delete_media_files / delete_product_files remove the on-disk directories

All file system writes target the app's own MEDIA_ROOT (an isolated temp dir per
test, courtesy of the `app` fixture), so tests are hermetic and order-independent.
"""

from __future__ import annotations

import io
import os
import subprocess
from typing import Any

import pytest
from PIL import Image

from app.services import media_service as ms


# --- helpers -----------------------------------------------------------------


def _make_jpeg_bytes(width: int, height: int, color: tuple[int, int, int] = (200, 80, 120)) -> bytes:
    """Return the bytes of a solid-color RGB JPEG of the given size."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _img_path(tmp_path: Any, width: int, height: int, *, mode: str = "RGB",
              fmt: str = "JPEG", name: str = "src") -> str:
    """Build an image of the given mode/size on disk and return its path.

    process_image() takes a FileStorage or a filesystem path (it calls
    Image.open on it), never an already-opened PIL Image — so tests must hand it
    a real source file.
    """
    color: Any = (10, 20, 30, 200) if mode == "RGBA" else (10, 20, 30)
    img = Image.new(mode, (width, height)) if mode == "P" else Image.new(mode, (width, height), color)
    ext = {"JPEG": "jpg", "PNG": "png"}.get(fmt, fmt.lower())
    path = str(tmp_path / f"{name}.{ext}")
    img.save(path, fmt)
    return path


def _media_root(app) -> str:
    with app.app_context():
        return app.config["MEDIA_ROOT"]


def _abs_dir(app, product_id: int, media_id: int) -> str:
    """Absolute directory the service would write for (product_id, media_id)."""
    return os.path.join(_media_root(app), "products", str(product_id), str(media_id))


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not available")


def _make_test_video(path: str, *, size: str = "320x568", duration: int = 1, rate: int = 15,
                     with_audio: bool = False) -> None:
    """Generate a small test video with ffmpeg lavfi (optionally with an audio track)."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate={rate}"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    args += ["-pix_fmt", "yuv420p"]
    if with_audio:
        args += ["-shortest"]
    args += [path]
    subprocess.run(args, check=True)


# --- ext_of ------------------------------------------------------------------


class TestExtOf:
    def test_simple_extension_lowercased(self) -> None:
        assert ms.ext_of("photo.JPG") == "jpg"
        assert ms.ext_of("clip.MP4") == "mp4"

    def test_already_lowercase(self) -> None:
        assert ms.ext_of("photo.jpeg") == "jpeg"

    def test_multiple_dots_uses_last_segment(self) -> None:
        assert ms.ext_of("my.holiday.photo.PnG") == "png"

    def test_no_extension_returns_empty(self) -> None:
        assert ms.ext_of("README") == ""

    def test_trailing_dot_returns_empty(self) -> None:
        # "name." splits into ["name", ""] -> last segment is empty
        assert ms.ext_of("name.") == ""

    def test_hidden_file_with_no_real_extension(self) -> None:
        # ".gitignore" rsplit on "." -> ["", "gitignore"]; the function treats the
        # part after the dot as the extension.
        assert ms.ext_of(".gitignore") == "gitignore"


# --- classify ----------------------------------------------------------------


class TestClassify:
    @pytest.mark.parametrize("name", [
        "a.jpg", "a.JPEG", "a.png", "a.PNG", "a.webp", "a.gif",
        "a.bmp", "a.tif", "a.tiff",
    ])
    def test_image_extensions(self, name: str) -> None:
        assert ms.classify(name) == "image"

    @pytest.mark.parametrize("name", [
        "a.mp4", "a.MOV", "a.m4v", "a.webm", "a.avi", "a.mkv",
        "a.3gp", "a.ogv", "a.mpg", "a.mpeg",
    ])
    def test_video_extensions(self, name: str) -> None:
        assert ms.classify(name) == "video"

    @pytest.mark.parametrize("name", ["doc.pdf", "archive.zip", "notes.txt", "noext", "weird.xyz"])
    def test_unknown_extensions_return_none(self, name: str) -> None:
        assert ms.classify(name) is None

    def test_case_insensitive(self) -> None:
        assert ms.classify("PHOTO.JpG") == "image"
        assert ms.classify("MOVIE.Mp4") == "video"

    def test_no_extension_returns_none(self) -> None:
        assert ms.classify("plainname") is None


# --- module constants --------------------------------------------------------


class TestModuleConstants:
    def test_image_widths(self) -> None:
        assert ms.IMAGE_WIDTHS == [400, 600, 1000]

    def test_max_image_dim(self) -> None:
        assert ms.MAX_IMAGE_DIM == 1600

    def test_video_max_dim(self) -> None:
        assert ms.VIDEO_MAX_DIM == 1280

    def test_media_error_is_exception(self) -> None:
        assert issubclass(ms.MediaError, Exception)


# --- process_image -----------------------------------------------------------


class TestProcessImage:
    def test_returns_expected_dict_for_medium_image(self, app, tmp_path) -> None:
        """A 1200x800 source (< MAX_IMAGE_DIM): widths are the IMAGE_WIDTHS below
        the source width plus the (uncapped) source width itself."""
        src = _img_path(tmp_path, 1200, 800)
        with app.app_context():
            result = ms.process_image(src, product_id=1, media_id=1)

        assert result["kind"] == "image"
        assert result["path"] == "products/1/1"
        assert result["width"] == 1200
        assert result["height"] == 800
        # 400, 600, 1000 are all < 1200, plus the source width 1200.
        assert result["widths"] == [400, 600, 1000, 1200]

    def test_writes_jpg_and_webp_for_each_width(self, app, tmp_path) -> None:
        src = _img_path(tmp_path, 1200, 800)
        with app.app_context():
            result = ms.process_image(src, product_id=2, media_id=5)

        abs_dir = _abs_dir(app, 2, 5)
        for w in result["widths"]:
            jpg = os.path.join(abs_dir, f"{w}.jpg")
            webp = os.path.join(abs_dir, f"{w}.webp")
            assert os.path.isfile(jpg), f"missing {jpg}"
            assert os.path.isfile(webp), f"missing {webp}"
            # And they must be readable as real images of the right width.
            with Image.open(jpg) as ji:
                assert ji.width == w
            with Image.open(webp) as wi:
                assert wi.width == w
        # Exactly the variants we expect: a jpg+webp per width, plus the 1200x630
        # social-share crop (og.jpg) used for og:image / twitter:image.
        files = sorted(os.listdir(abs_dir))
        expected = sorted(
            [f"{w}.jpg" for w in result["widths"]]
            + [f"{w}.webp" for w in result["widths"]]
            + ["og.jpg"]
        )
        assert files == expected
        # The og crop is exactly 1200x630.
        with Image.open(os.path.join(abs_dir, "og.jpg")) as og:
            assert og.size == (1200, 630)

    def test_small_image_only_source_width_plus_smaller_widths(self, app, tmp_path) -> None:
        """A 500-wide source: only 400 is < 500, so widths are [400, 500]."""
        src = _img_path(tmp_path, 500, 500)
        with app.app_context():
            result = ms.process_image(src, product_id=3, media_id=1)
        assert result["widths"] == [400, 500]
        assert result["width"] == 500

    def test_tiny_image_only_its_own_width(self, app, tmp_path) -> None:
        """A 300-wide source: none of IMAGE_WIDTHS are < 300, so widths == [300]."""
        src = _img_path(tmp_path, 300, 300)
        with app.app_context():
            result = ms.process_image(src, product_id=4, media_id=1)
        assert result["widths"] == [300]
        abs_dir = _abs_dir(app, 4, 1)
        assert os.path.isfile(os.path.join(abs_dir, "300.jpg"))
        assert os.path.isfile(os.path.join(abs_dir, "300.webp"))

    def test_caps_oversized_landscape_image(self, app, tmp_path) -> None:
        """A 2000x1000 source is capped so the longest side == MAX_IMAGE_DIM."""
        src = _img_path(tmp_path, 2000, 1000)
        with app.app_context():
            result = ms.process_image(src, product_id=5, media_id=1)
        # thumbnail keeps aspect: 2000x1000 -> 1600x800.
        assert result["width"] == ms.MAX_IMAGE_DIM == 1600
        assert result["height"] == 800
        # 400/600/1000 all < 1600, plus the capped source width.
        assert result["widths"] == [400, 600, 1000, 1600]

    def test_caps_oversized_portrait_image(self, app, tmp_path) -> None:
        """A 1000x2000 source caps the height; width becomes 800."""
        src = _img_path(tmp_path, 1000, 2000)
        with app.app_context():
            result = ms.process_image(src, product_id=6, media_id=1)
        assert result["width"] == 800
        assert result["height"] == ms.MAX_IMAGE_DIM == 1600
        # Only 400 and 600 are < 800, plus the (capped) source width 800.
        assert result["widths"] == [400, 600, 800]

    def test_handles_png_with_alpha_channel(self, app, tmp_path) -> None:
        """An RGBA PNG must be converted to RGB and processed (JPEG can't hold alpha)."""
        src = _img_path(tmp_path, 800, 600, mode="RGBA", fmt="PNG")
        with app.app_context():
            result = ms.process_image(src, product_id=7, media_id=1)
        assert result["kind"] == "image"
        assert result["width"] == 800
        abs_dir = _abs_dir(app, 7, 1)
        # JPEG variant must exist and be a valid RGB image.
        jpg = os.path.join(abs_dir, "600.jpg")  # 600 < 800
        assert os.path.isfile(jpg)
        with Image.open(jpg) as ji:
            assert ji.mode == "RGB"

    def test_handles_palette_mode_image(self, app, tmp_path) -> None:
        """A palette ('P') mode GIF-like image is converted to RGB and processed."""
        src = _img_path(tmp_path, 700, 500, mode="P", fmt="PNG")
        with app.app_context():
            result = ms.process_image(src, product_id=8, media_id=1)
        assert result["kind"] == "image"
        assert result["width"] == 700

    def test_accepts_filestorage_source(self, app) -> None:
        """The service accepts a werkzeug FileStorage (the real upload path)."""
        from werkzeug.datastructures import FileStorage

        with app.app_context():
            fs = FileStorage(
                stream=io.BytesIO(_make_jpeg_bytes(900, 600)),
                filename="foto.jpg",
                content_type="image/jpeg",
            )
            result = ms.process_image(fs, product_id=9, media_id=1)
        assert result["width"] == 900
        assert result["widths"] == [400, 600, 900]

    def test_accepts_path_source(self, app, tmp_path) -> None:
        """The service accepts a plain filesystem path as the source."""
        src = tmp_path / "src.jpg"
        src.write_bytes(_make_jpeg_bytes(1100, 700))
        with app.app_context():
            result = ms.process_image(str(src), product_id=10, media_id=1)
        assert result["width"] == 1100
        assert result["widths"] == [400, 600, 1000, 1100]

    def test_respects_exif_orientation(self, app, tmp_path) -> None:
        """An EXIF orientation=6 (rotate 90 CW) image is transposed: a 400x800
        physical buffer should be presented as 800x400 after exif_transpose."""
        # Build a portrait 400x800 image, but tag it with orientation 6 so that the
        # *intended* display is landscape 800x400.
        img = Image.new("RGB", (400, 800), (50, 100, 150))
        exif = img.getexif()
        exif[274] = 6  # 274 == Orientation tag; 6 = rotate 90 CW on display
        src = tmp_path / "rotated.jpg"
        img.save(str(src), "JPEG", exif=exif)

        with app.app_context():
            result = ms.process_image(str(src), product_id=11, media_id=1)
        # After exif_transpose the displayed image is landscape: width 800, height 400.
        assert result["width"] == 800
        assert result["height"] == 400

    def test_raises_media_error_on_corrupt_file_and_cleans_dir(self, app, tmp_path) -> None:
        """A non-image file raises MediaError and the created dir is removed."""
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"this is definitely not a JPEG, just random text\x00\x01\x02")
        with app.app_context():
            with pytest.raises(ms.MediaError):
                ms.process_image(str(bad), product_id=12, media_id=1)
        # The directory the service makes up-front must be cleaned on failure.
        abs_dir = _abs_dir(app, 12, 1)
        assert not os.path.exists(abs_dir)

    def test_raises_media_error_on_empty_file(self, app) -> None:
        from werkzeug.datastructures import FileStorage

        with app.app_context():
            fs = FileStorage(stream=io.BytesIO(b""), filename="empty.jpg")
            with pytest.raises(ms.MediaError):
                ms.process_image(fs, product_id=13, media_id=1)
        assert not os.path.exists(_abs_dir(app, 13, 1))

    def test_jpeg_variants_are_smaller_or_equal_in_dimension(self, app, tmp_path) -> None:
        """Each generated variant's pixel width matches its filename width."""
        src = _img_path(tmp_path, 1500, 1000)
        with app.app_context():
            result = ms.process_image(src, product_id=14, media_id=1)
        abs_dir = _abs_dir(app, 14, 1)
        for w in result["widths"]:
            with Image.open(os.path.join(abs_dir, f"{w}.jpg")) as ji:
                assert ji.width == w
                # aspect preserved: 1500x1000 => 3:2
                assert ji.height == round(1000 * w / 1500)


# --- process_video -----------------------------------------------------------


@requires_ffmpeg
class TestProcessVideo:
    def test_produces_mp4_and_poster(self, app, tmp_path) -> None:
        src = str(tmp_path / "clip.mp4")
        _make_test_video(src, size="320x568")
        with app.app_context():
            result = ms.process_video(src, product_id=20, media_id=1)

        assert result["kind"] == "video"
        assert result["path"] == "products/20/1"
        assert result["widths"] is None
        abs_dir = _abs_dir(app, 20, 1)
        assert os.path.isfile(os.path.join(abs_dir, "video.mp4"))
        assert os.path.isfile(os.path.join(abs_dir, "poster.jpg"))

    def test_returns_dimensions_matching_poster(self, app, tmp_path) -> None:
        src = str(tmp_path / "clip.mp4")
        _make_test_video(src, size="320x568")
        with app.app_context():
            result = ms.process_video(src, product_id=21, media_id=1)
        # A 320x568 input is already <= 1280 on both sides, so dims are preserved.
        assert result["width"] == 320
        assert result["height"] == 568
        # And they match the poster image exactly.
        with Image.open(os.path.join(_abs_dir(app, 21, 1), "poster.jpg")) as poster:
            assert (poster.width, poster.height) == (320, 568)

    def test_scales_oversized_video_longest_side_to_1280(self, app, tmp_path) -> None:
        """A 1920x1080 input is scaled so the longest side <= VIDEO_MAX_DIM, even dims."""
        src = str(tmp_path / "big.mp4")
        _make_test_video(src, size="1920x1080")
        with app.app_context():
            result = ms.process_video(src, product_id=22, media_id=1)
        assert max(result["width"], result["height"]) <= ms.VIDEO_MAX_DIM
        # 1920x1080 -> aspect preserved -> 1280x720.
        assert result["width"] == 1280
        assert result["height"] == 720
        # Even dimensions are required for yuv420p.
        assert result["width"] % 2 == 0
        assert result["height"] % 2 == 0

    def test_scales_oversized_portrait_video(self, app, tmp_path) -> None:
        """A tall 1080x1920 input scales the height down to 1280; even dims."""
        src = str(tmp_path / "tall.mp4")
        _make_test_video(src, size="1080x1920")
        with app.app_context():
            result = ms.process_video(src, product_id=23, media_id=1)
        assert max(result["width"], result["height"]) <= ms.VIDEO_MAX_DIM
        assert result["height"] == 1280
        assert result["width"] == 720
        assert result["width"] % 2 == 0
        assert result["height"] % 2 == 0

    def test_does_not_upscale_small_video(self, app, tmp_path) -> None:
        """A small input is never upscaled (force_original_aspect_ratio=decrease)."""
        src = str(tmp_path / "small.mp4")
        _make_test_video(src, size="160x120")
        with app.app_context():
            result = ms.process_video(src, product_id=24, media_id=1)
        assert result["width"] == 160
        assert result["height"] == 120

    def test_strips_audio_track(self, app, tmp_path) -> None:
        """The output MP4 must have no audio stream (-an)."""
        src = str(tmp_path / "withsound.mp4")
        _make_test_video(src, size="320x240", with_audio=True)
        with app.app_context():
            ms.process_video(src, product_id=25, media_id=1)
        out = os.path.join(_abs_dir(app, 25, 1), "video.mp4")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
            capture_output=True, text=True,
        )
        # No audio stream -> ffprobe prints nothing.
        assert probe.stdout.strip() == ""

    def test_output_video_codec_is_h264(self, app, tmp_path) -> None:
        src = str(tmp_path / "clip.mp4")
        _make_test_video(src, size="320x240")
        with app.app_context():
            ms.process_video(src, product_id=26, media_id=1)
        out = os.path.join(_abs_dir(app, 26, 1), "video.mp4")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", out],
            capture_output=True, text=True,
        )
        assert probe.stdout.strip() == "h264"

    def test_accepts_filestorage_video(self, app, tmp_path) -> None:
        """The real upload path: a FileStorage is streamed to a temp file first."""
        from werkzeug.datastructures import FileStorage

        src = str(tmp_path / "upload.mp4")
        _make_test_video(src, size="320x568")
        with open(src, "rb") as fh:
            data = fh.read()
        with app.app_context():
            fs = FileStorage(stream=io.BytesIO(data), filename="upload.mp4",
                             content_type="video/mp4")
            result = ms.process_video(fs, product_id=27, media_id=1)
        assert result["width"] == 320
        assert result["height"] == 568
        assert os.path.isfile(os.path.join(_abs_dir(app, 27, 1), "video.mp4"))

    def test_raises_media_error_on_bad_video_and_cleans_dir(self, app, tmp_path) -> None:
        """A non-video file makes ffmpeg fail -> MediaError, dir cleaned up."""
        bad = str(tmp_path / "bad.mp4")
        with open(bad, "wb") as fh:
            fh.write(b"not a real video file at all\x00\x01")
        with app.app_context():
            with pytest.raises(ms.MediaError):
                ms.process_video(bad, product_id=28, media_id=1)
        assert not os.path.exists(_abs_dir(app, 28, 1))


# --- cleanup -----------------------------------------------------------------


class TestCleanup:
    def test_delete_media_files_removes_directory(self, app, tmp_path) -> None:
        src = _img_path(tmp_path, 800, 600)
        with app.app_context():
            result = ms.process_image(src, product_id=30, media_id=3)
            abs_dir = os.path.join(app.config["MEDIA_ROOT"], result["path"])
            assert os.path.isdir(abs_dir)
            ms.delete_media_files(result["path"])
            assert not os.path.exists(abs_dir)

    def test_delete_media_files_is_safe_on_missing_dir(self, app) -> None:
        """Deleting a non-existent rel_dir must not raise (ignore_errors=True)."""
        with app.app_context():
            ms.delete_media_files("products/999/999")  # no exception expected

    def test_delete_product_files_removes_whole_product_tree(self, app, tmp_path) -> None:
        src = _img_path(tmp_path, 800, 600)
        with app.app_context():
            ms.process_image(src, product_id=31, media_id=1)
            ms.process_image(src, product_id=31, media_id=2)
            product_dir = os.path.join(app.config["MEDIA_ROOT"], "products", "31")
            assert os.path.isdir(product_dir)
            assert os.path.isdir(os.path.join(product_dir, "1"))
            assert os.path.isdir(os.path.join(product_dir, "2"))
            ms.delete_product_files(31)
            assert not os.path.exists(product_dir)

    def test_delete_product_files_is_safe_on_missing_product(self, app) -> None:
        with app.app_context():
            ms.delete_product_files(123456)  # no exception expected
