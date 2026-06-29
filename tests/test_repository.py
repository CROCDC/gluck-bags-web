"""Unit tests for ProductRepository (app.repositories.product_repository).

These exercise the data-access layer directly against a fresh, empty, isolated
DB (the `app` fixture from conftest gives each test its own temp DATA_DIR with
SEED_PRODUCTS=0). All DB work runs inside an app context.

Covered: get_published (only published, ordered position then id), get_all (all,
position order), get_by_id (hit / miss), next_position (monotonic), create
(persistence, defaults, increasing position), save (commits mutations), delete
(removes the row and cascade-deletes its Media).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.factory import db
from app.models import Media, Product
from app.repositories import ProductRepository

if TYPE_CHECKING:
    from flask import Flask


# --- helpers -----------------------------------------------------------------


def _add_product(
    *,
    title: str,
    position: int,
    is_published: bool = True,
) -> Product:
    """Insert a product straight through the ORM with an explicit position.

    Bypasses ProductRepository.create() on purpose so ordering/filtering tests
    don't depend on the very method they may be probing.
    """
    product = Product(
        title=title,
        description="",
        price=None,
        is_published=is_published,
        position=position,
    )
    db.session.add(product)
    db.session.commit()
    return product


def _add_media(product: Product, *, kind: str = "image") -> Media:
    media = Media(product_id=product.id, kind=kind, path="products/x/y", position=0)
    db.session.add(media)
    db.session.commit()
    return media


# --- get_published -----------------------------------------------------------


def test_get_published_returns_only_published(app: "Flask") -> None:
    with app.app_context():
        _add_product(title="Visible A", position=0, is_published=True)
        _add_product(title="Hidden", position=1, is_published=False)
        _add_product(title="Visible B", position=2, is_published=True)

        published = ProductRepository.get_published()

        titles = [p.title for p in published]
        assert titles == ["Visible A", "Visible B"]
        assert all(p.is_published for p in published)


def test_get_published_empty_db_returns_empty_list(app: "Flask") -> None:
    with app.app_context():
        assert ProductRepository.get_published() == []


def test_get_published_ordered_by_position_then_id(app: "Flask") -> None:
    """Lower position first; ties broken by ascending id (insertion order)."""
    with app.app_context():
        # Insert out of position order, and give two rows the SAME position so
        # the id tie-breaker is what decides their relative order.
        third = _add_product(title="pos2", position=2, is_published=True)
        first = _add_product(title="pos0-first", position=0, is_published=True)
        second = _add_product(title="pos0-second", position=0, is_published=True)

        published = ProductRepository.get_published()
        ids = [p.id for p in published]

        # position 0 rows come before position 2; within position 0 the lower id
        # (inserted first) wins.
        assert ids == [first.id, second.id, third.id]


def test_get_published_excludes_all_when_none_published(app: "Flask") -> None:
    with app.app_context():
        _add_product(title="h1", position=0, is_published=False)
        _add_product(title="h2", position=1, is_published=False)
        assert ProductRepository.get_published() == []


# --- get_all -----------------------------------------------------------------


def test_get_all_returns_published_and_unpublished(app: "Flask") -> None:
    with app.app_context():
        _add_product(title="pub", position=0, is_published=True)
        _add_product(title="unpub", position=1, is_published=False)

        everything = ProductRepository.get_all()
        titles = {p.title for p in everything}
        assert titles == {"pub", "unpub"}
        assert len(everything) == 2


def test_get_all_ordered_by_position_then_id(app: "Flask") -> None:
    with app.app_context():
        p_b = _add_product(title="B", position=1, is_published=False)
        p_a = _add_product(title="A", position=0, is_published=True)
        p_c_lo = _add_product(title="C-first", position=5, is_published=True)
        p_c_hi = _add_product(title="C-second", position=5, is_published=False)

        ordered = ProductRepository.get_all()
        assert [p.id for p in ordered] == [p_a.id, p_b.id, p_c_lo.id, p_c_hi.id]


def test_get_all_empty_db_returns_empty_list(app: "Flask") -> None:
    with app.app_context():
        assert ProductRepository.get_all() == []


# --- get_by_id ---------------------------------------------------------------


def test_get_by_id_returns_matching_product(app: "Flask") -> None:
    with app.app_context():
        created = _add_product(title="Findable", position=0)
        found = ProductRepository.get_by_id(created.id)
        assert found is not None
        assert found.id == created.id
        assert found.title == "Findable"


def test_get_by_id_missing_returns_none(app: "Flask") -> None:
    with app.app_context():
        # Nothing inserted, so any id is missing.
        assert ProductRepository.get_by_id(999999) is None


def test_get_by_id_after_delete_returns_none(app: "Flask") -> None:
    with app.app_context():
        created = _add_product(title="Gone soon", position=0)
        product_id = created.id
        db.session.delete(created)
        db.session.commit()
        assert ProductRepository.get_by_id(product_id) is None


# --- next_position -----------------------------------------------------------


def test_next_position_on_empty_db_is_zero(app: "Flask") -> None:
    with app.app_context():
        assert ProductRepository.next_position() == 0


def test_next_position_is_one_past_the_highest(app: "Flask") -> None:
    with app.app_context():
        _add_product(title="a", position=0)
        _add_product(title="b", position=1)
        _add_product(title="c", position=2)
        assert ProductRepository.next_position() == 3


def test_next_position_uses_max_position_not_count(app: "Flask") -> None:
    """It tracks the highest position, regardless of how many rows or gaps."""
    with app.app_context():
        _add_product(title="a", position=0)
        _add_product(title="big", position=10)  # gap in between
        assert ProductRepository.next_position() == 11


def test_next_position_ignores_publish_state(app: "Flask") -> None:
    with app.app_context():
        _add_product(title="hidden-high", position=7, is_published=False)
        assert ProductRepository.next_position() == 8


# --- create ------------------------------------------------------------------


def test_create_persists_with_given_fields(app: "Flask") -> None:
    with app.app_context():
        product = ProductRepository.create(
            title="Tote Negro",
            description="Cuero vegano",
            price=45000,
            category="Tote",
            is_published=False,
        )
        # Returned object has the fields...
        assert product.id is not None
        assert product.title == "Tote Negro"
        assert product.description == "Cuero vegano"
        assert product.price == 45000
        assert product.category == "Tote"
        assert product.is_published is False

        # ...and it is actually committed to the DB (re-fetch by id).
        fetched = db.session.get(Product, product.id)
        assert fetched is not None
        assert fetched.title == "Tote Negro"
        assert fetched.price == 45000
        assert fetched.category == "Tote"
        assert fetched.is_published is False


def test_create_applies_sane_defaults(app: "Flask") -> None:
    with app.app_context():
        product = ProductRepository.create(title="Minimal")
        assert product.description == ""
        assert product.price is None
        assert product.category is None
        assert product.is_published is True
        assert product.currency == "ARS"


def test_create_normalizes_blank_description_and_category(app: "Flask") -> None:
    """Empty strings become "" for description and None for category."""
    with app.app_context():
        product = ProductRepository.create(
            title="Edge", description="", category=""
        )
        assert product.description == ""
        # create() does `category or None`, so an empty string is stored as NULL.
        assert product.category is None


def test_create_assigns_increasing_positions(app: "Flask") -> None:
    with app.app_context():
        first = ProductRepository.create(title="first")
        second = ProductRepository.create(title="second")
        third = ProductRepository.create(title="third")
        assert first.position == 0
        assert second.position == 1
        assert third.position == 2


def test_create_position_continues_past_existing_rows(app: "Flask") -> None:
    """A freshly created product takes the slot after the current max position."""
    with app.app_context():
        _add_product(title="seeded", position=4)
        created = ProductRepository.create(title="new one")
        assert created.position == 5


def test_create_sets_timestamps(app: "Flask") -> None:
    with app.app_context():
        product = ProductRepository.create(title="Stamped")
        assert product.created_at is not None
        assert product.updated_at is not None


# --- save --------------------------------------------------------------------


def test_save_commits_field_mutations(app: "Flask") -> None:
    with app.app_context():
        product = ProductRepository.create(title="Old title", price=None)
        product.title = "New title"
        product.price = 99000
        product.is_published = False
        ProductRepository.save()

        # Expire the identity map so we read straight from the DB.
        product_id = product.id
        db.session.expire_all()
        fetched = db.session.get(Product, product_id)
        assert fetched is not None
        assert fetched.title == "New title"
        assert fetched.price == 99000
        assert fetched.is_published is False


def test_save_persists_across_a_fresh_session(app: "Flask") -> None:
    """Mutations survive even after the session is removed (truly committed)."""
    with app.app_context():
        product = ProductRepository.create(title="Persist me")
        product_id = product.id
        product.category = "Clutch"
        ProductRepository.save()
        # Drop the session entirely so nothing is cached.
        db.session.remove()
        fetched = db.session.get(Product, product_id)
        assert fetched is not None
        assert fetched.category == "Clutch"


# --- delete ------------------------------------------------------------------


def test_delete_removes_the_row(app: "Flask") -> None:
    with app.app_context():
        product = ProductRepository.create(title="Delete me")
        product_id = product.id
        ProductRepository.delete(product)
        assert db.session.get(Product, product_id) is None
        assert Product.query.count() == 0


def test_delete_leaves_other_products_intact(app: "Flask") -> None:
    with app.app_context():
        keep = ProductRepository.create(title="keep")
        drop = ProductRepository.create(title="drop")
        ProductRepository.delete(drop)

        remaining = ProductRepository.get_all()
        assert [p.id for p in remaining] == [keep.id]


def test_delete_cascade_removes_media_rows(app: "Flask") -> None:
    """Deleting a product also deletes its Media rows (cascade delete-orphan)."""
    with app.app_context():
        product = ProductRepository.create(title="With media")
        m1 = _add_media(product, kind="image")
        m2 = _add_media(product, kind="video")
        media_ids = [m1.id, m2.id]
        assert Media.query.count() == 2

        ProductRepository.delete(product)

        assert Media.query.count() == 0
        for media_id in media_ids:
            assert db.session.get(Media, media_id) is None


def test_delete_cascade_does_not_touch_other_products_media(app: "Flask") -> None:
    with app.app_context():
        victim = ProductRepository.create(title="victim")
        survivor = ProductRepository.create(title="survivor")
        _add_media(victim)
        survivor_media = _add_media(survivor)

        ProductRepository.delete(victim)

        assert Media.query.count() == 1
        assert db.session.get(Media, survivor_media.id) is not None
