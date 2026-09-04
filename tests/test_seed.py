"""Integration tests for the one-time catalogue seed (app/seed.py).

Seeding is eager: it runs inside create_app() via _initialize_schema when the
products table is empty and SEED_PRODUCTS != "0". So here the action under test is
simply constructing the app under the right env. Each test gets its own isolated
DATA_DIR (tmp_path) to avoid cross-test bleed, and asserts the observable result
of the seed: the rows it writes and the responsive image variants it lays on disk.
"""

from __future__ import annotations

import os

import app.seed as seed_module
from app.factory import create_app
from app.models import Media, Product


def _build_seeded_app(tmp_path, monkeypatch, *, seed: str = "1", data_subdir: str = "data"):
    """create_app() against an isolated DATA_DIR; seeding happens during construction."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / data_subdir))
    monkeypatch.setenv("SEED_PRODUCTS", seed)
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    monkeypatch.setenv("SECRET_KEY", "k")
    return create_app()


def _seed_count() -> int:
    """All _SEED stems ship their source image under static/, so all should seed."""
    return len(seed_module._SEED)


# --- 1) Seed on an empty DB ---------------------------------------------------


def test_seed_populates_empty_db(tmp_path, monkeypatch) -> None:
    app = _build_seeded_app(tmp_path, monkeypatch)
    expected = _seed_count()
    assert expected == 6  # guard: seed mirrors the 6-product pre-migration lookbook

    with app.app_context():
        products = Product.query.order_by(Product.position).all()
        assert len(products) == expected

        # Order + per-product invariants mirror seed.py (the legacy admin source).
        for index, (product, (title, category, _stem)) in enumerate(
            zip(products, seed_module._SEED)
        ):
            assert product.title == title
            assert product.category == category
            assert isinstance(product.category, str) and product.category
            assert product.is_published is True
            assert product.price is None  # legacy lookbook showed "Consultar"
            assert product.description is None  # the legacy lookbook had none
            assert product.position == index

            # Exactly one media row, the cover image.
            assert len(product.media) == 1
            cover = product.media[0]
            assert cover.is_cover is True
            assert cover.kind == "image"
            assert cover.position == 0

            # On-disk responsive variants exist under MEDIA_ROOT/<media.path>.
            media_dir = os.path.join(app.config["MEDIA_ROOT"], cover.path)
            assert os.path.isdir(media_dir)
            files = os.listdir(media_dir)
            assert any(f.endswith(".jpg") for f in files)
            assert any(f.endswith(".webp") for f in files)


# --- 2) Seed disabled ---------------------------------------------------------


def test_seed_disabled_leaves_db_empty(tmp_path, monkeypatch) -> None:
    app = _build_seeded_app(tmp_path, monkeypatch, seed="0")
    with app.app_context():
        assert Product.query.count() == 0


# --- 3) Idempotent ------------------------------------------------------------


def test_seed_is_idempotent_across_restarts(tmp_path, monkeypatch) -> None:
    """A second create_app() against the same DATA_DIR must not double-seed."""
    first = _build_seeded_app(tmp_path, monkeypatch)
    with first.app_context():
        first_count = Product.query.count()
    assert first_count == _seed_count()

    # Same DATA_DIR (same subdir) -> table is no longer empty -> early return.
    second = _build_seeded_app(tmp_path, monkeypatch)
    with second.app_context():
        assert Product.query.count() == first_count


# --- 4) Missing source branch -------------------------------------------------


def test_missing_source_skips_only_that_product(tmp_path, monkeypatch) -> None:
    """Cover seed.py's `if not source: continue`: one stem resolving to None drops
    exactly that product while the rest still seed."""
    real_find_source = seed_module._find_source
    skipped_stem = seed_module._SEED[2][2]  # Crossbody Rosa

    def fake_find_source(static_dir: str, stem: str):
        if stem == skipped_stem:
            return None
        return real_find_source(static_dir, stem)

    monkeypatch.setattr(seed_module, "_find_source", fake_find_source)

    app = _build_seeded_app(tmp_path, monkeypatch)
    expected = _seed_count() - 1
    with app.app_context():
        assert Product.query.count() == expected
        titles = {p.title for p in Product.query.all()}
        assert seed_module._SEED[2][0] not in titles  # the skipped one is absent
        # The others are present, so only one was dropped (not a broad failure).
        assert seed_module._SEED[0][0] in titles
        assert seed_module._SEED[-1][0] in titles


# --- 4) Startup self-heals media variants (og.jpg + AVIF) ---------------------


def test_startup_backfills_missing_media_variants(tmp_path, monkeypatch) -> None:
    """A redeploy heals media in place: if og.jpg / AVIF are missing on the volume
    (products seeded by an older build), constructing the app regenerates them — so
    no manual reprocess is ever run on the server."""
    from app.maintenance import _avif_supported

    app = _build_seeded_app(tmp_path, monkeypatch)
    with app.app_context():
        media = Media.query.filter_by(kind="image").first()
        assert media is not None
        d = os.path.join(app.config["MEDIA_ROOT"], media.path)
        assert os.path.exists(os.path.join(d, "og.jpg"))  # seed generated it
        # Simulate an old volume: drop og.jpg + every AVIF variant.
        os.remove(os.path.join(d, "og.jpg"))
        removed_avif = [f for f in os.listdir(d) if f.endswith(".avif")]
        for f in removed_avif:
            os.remove(os.path.join(d, f))
        media_path = media.path

    # Re-construct against the SAME DATA_DIR (a "redeploy"): the startup backfill runs.
    app2 = _build_seeded_app(tmp_path, monkeypatch)
    with app2.app_context():
        d = os.path.join(app2.config["MEDIA_ROOT"], media_path)
        assert os.path.exists(os.path.join(d, "og.jpg")), "og.jpg not healed on boot"
        if _avif_supported() and removed_avif:
            for f in removed_avif:
                assert os.path.exists(os.path.join(d, f)), f"AVIF {f} not healed on boot"


def test_backfill_skips_corrupt_file_and_heals_the_rest(tmp_path, monkeypatch) -> None:
    """One unreadable JPEG must not abort the whole self-heal: that file is counted
    as failed and skipped while every other variant is still regenerated, and og.jpg
    is rebuilt from a larger (intact) source."""
    import pytest

    from app.maintenance import _avif_supported, backfill_media_variants

    if not _avif_supported():
        pytest.skip("Pillow build has no AVIF encoder")

    app = _build_seeded_app(tmp_path, monkeypatch)
    with app.app_context():
        root = app.config["MEDIA_ROOT"]
        media = Media.query.filter_by(kind="image").first()
        d = os.path.join(root, media.path)
        widths = sorted(media.widths)
        assert len(widths) >= 2, "need a multi-width image for this test"

        # Simulate an old volume + one corrupt input.
        os.remove(os.path.join(d, "og.jpg"))
        for f in [f for f in os.listdir(d) if f.endswith(".avif")]:
            os.remove(os.path.join(d, f))
        smallest = widths[0]
        with open(os.path.join(d, f"{smallest}.jpg"), "wb") as fh:
            fh.write(b"not a real jpeg")

        counts = backfill_media_variants()

        # The corrupt width's AVIF failed; the rest healed; og.jpg rebuilt from a
        # larger intact jpg — the backfill did NOT abort on the bad file.
        assert counts["failed"] >= 1
        assert os.path.exists(os.path.join(d, "og.jpg"))
        for w in widths[1:]:
            assert os.path.exists(os.path.join(d, f"{w}.avif")), f"{w}.avif not healed"
        assert not os.path.exists(os.path.join(d, f"{smallest}.avif"))
