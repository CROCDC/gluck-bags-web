"""The app against a REAL Postgres, which is what the serverless deploy will run on.

Dimension: postgres. Everything else in this suite runs on SQLite, so nothing here is
about business logic — it is about the things that differ between the two engines and
that would only surface after the cutover:

- the connection string a provider hands out is not the one SQLAlchemy accepts;
- the SQLite PRAGMA hook fires on EVERY connection, including Postgres ones;
- `db.create_all()` has to produce a working schema (JSON columns above all);
- the JSON columns on TiendaNubeProduct round-trip through `jsonb`, not through
  SQLite's "JSON is really TEXT".

Skipped unless TEST_DATABASE_URL points at a reachable Postgres, so the default suite
is unaffected:

    docker run -d --name gluck-pg-test -e POSTGRES_PASSWORD=gluck -e POSTGRES_USER=gluck \\
        -e POSTGRES_DB=gluck -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://gluck:gluck@localhost:55432/gluck pytest tests/test_postgres.py

Each test gets its own Postgres SCHEMA, so they are as isolated from each other as the
per-test SQLite files are.
"""

from __future__ import annotations

import os
import uuid

import pytest

RAW_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not RAW_URL, reason="TEST_DATABASE_URL not set; see this module's docstring"
)


@pytest.fixture
def pg_app(tmp_path, monkeypatch):
    """A fresh app on its own Postgres schema, bootstrapped via `flask init-db`."""
    from app.factory import _normalize_database_url

    schema = f"t_{uuid.uuid4().hex[:16]}"
    url = _normalize_database_url(RAW_URL)

    import sqlalchemy

    engine = sqlalchemy.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.schema.CreateSchema(schema))
    engine.dispose()

    # search_path scopes every unqualified table to this test's schema, so the models
    # need no changes and the tests stay independent.
    monkeypatch.setenv("DATABASE_URL", f"{RAW_URL}?options=-csearch_path%3D{schema}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("AUTO_INIT_DB", "0")

    from app.factory import create_app

    application = create_app()
    application.testing = True
    result = application.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output

    yield application

    from app.factory import db

    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    engine = sqlalchemy.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.schema.DropSchema(schema, cascade=True))
    engine.dispose()


# --- connection string --------------------------------------------------------


class TestNormalizeDatabaseUrl:
    """Pure string handling, so it runs without a database."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            (
                "postgres://u:p@host/db",
                "postgresql+psycopg://u:p@host/db",
            ),
            (
                "postgresql://u:p@host/db",
                "postgresql+psycopg://u:p@host/db",
            ),
            (
                "postgresql+psycopg://u:p@host/db",
                "postgresql+psycopg://u:p@host/db",
            ),
            ("sqlite:////tmp/x.db", "sqlite:////tmp/x.db"),
        ],
    )
    def test_rewrites_provider_schemes(self, given: str, expected: str) -> None:
        """Neon hands out `postgres://`, which SQLAlchemy 2 rejects, and a bare
        `postgresql://` resolves to psycopg2, which is not installed."""
        from app.factory import _normalize_database_url

        assert _normalize_database_url(given) == expected

    def test_preserves_query_parameters(self) -> None:
        """Neon's pooled string carries `?sslmode=require`; dropping it breaks TLS."""
        from app.factory import _normalize_database_url

        result = _normalize_database_url("postgres://u:p@host/db?sslmode=require")

        assert result.endswith("?sslmode=require")


# --- wiring -------------------------------------------------------------------


class TestConfiguration:
    def test_database_url_wins_over_the_sqlite_default(self, pg_app) -> None:
        assert pg_app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+psycopg://")

    def test_sqlite_connect_args_are_not_sent_to_postgres(self, pg_app) -> None:
        """`timeout` is a sqlite3 connect arg; psycopg rejects it outright, so leaving
        it in would make the app fail to open a single connection."""
        options = pg_app.config["SQLALCHEMY_ENGINE_OPTIONS"]

        assert "connect_args" not in options
        assert options["pool_pre_ping"] is True

    def test_the_engine_actually_connects(self, pg_app) -> None:
        from sqlalchemy import text

        from app.factory import db

        with pg_app.app_context():
            assert db.session.execute(text("select 1")).scalar() == 1

    def test_pragma_hook_leaves_the_connection_usable(self, pg_app) -> None:
        """The SQLite PRAGMA listener fires on every new connection, Postgres included.
        A PRAGMA is a syntax error there, and it aborts the transaction the connection
        just opened — so an unguarded hook makes the NEXT statement fail too."""
        from sqlalchemy import text

        from app.factory import db

        with pg_app.app_context():
            db.engine.dispose()  # force a brand-new connection through the hook
            assert db.session.execute(text("select 42")).scalar() == 42


# --- schema -------------------------------------------------------------------


class TestSchema:
    def test_init_db_creates_every_table(self, pg_app) -> None:
        from sqlalchemy import inspect

        from app.factory import db

        with pg_app.app_context():
            tables = set(inspect(db.engine).get_table_names())

        assert {"products", "product_media", "tiendanube_products", "site_texts"} <= tables

    def test_json_columns_are_real_json(self, pg_app) -> None:
        """SQLite stores JSON as TEXT and would pass a round-trip test regardless.
        On Postgres the column type has to actually be json/jsonb."""
        from sqlalchemy import inspect

        from app.factory import db

        with pg_app.app_context():
            columns = {
                c["name"]: str(c["type"]).upper()
                for c in inspect(db.engine).get_columns("tiendanube_products")
            }

        for name in ("variants", "images", "raw"):
            assert "JSON" in columns[name], (name, columns[name])


# --- data ---------------------------------------------------------------------


class TestRoundTrips:
    def test_product_and_media_round_trip(self, pg_app) -> None:
        from app.factory import db
        from app.models import Media, Product

        with pg_app.app_context():
            product = Product(title="Bolso", price=45000, is_published=True)
            db.session.add(product)
            db.session.flush()
            db.session.add(
                Media(
                    product_id=product.id,
                    kind="image",
                    path=f"products/{product.id}/1",
                    width=1000,
                    height=800,
                    widths=[400, 600, 1000],
                    is_cover=True,
                )
            )
            db.session.commit()

            saved = Product.query.filter_by(title="Bolso").one()
            assert saved.media[0].widths == [400, 600, 1000]
            assert saved.media[0].thumb_url.endswith("400.webp")

    def test_tiendanube_json_payloads_round_trip(self, pg_app) -> None:
        """The nested dict/list shape is what the mirror actually stores; a driver that
        silently stringified it would break `localized()` and every variant lookup."""
        from app.factory import db
        from app.models import TiendaNubeProduct

        raw = {"id": 7, "name": {"es": "Bolso", "pt": "Bolsa"}, "tags": ["a", "b"]}
        with pg_app.app_context():
            db.session.add(
                TiendaNubeProduct(
                    tn_id=7,
                    name="Bolso",
                    variants=[{"id": 70, "price": "1000.00", "stock": 3}],
                    images=["https://example.test/a.jpg"],
                    raw=raw,
                )
            )
            db.session.commit()

            saved = TiendaNubeProduct.query.filter_by(tn_id=7).one()
            assert saved.raw == raw
            assert saved.raw["name"]["es"] == "Bolso"
            assert saved.variants[0]["stock"] == 3
            assert saved.images == ["https://example.test/a.jpg"]

    def test_unique_constraint_is_enforced(self, pg_app) -> None:
        from sqlalchemy.exc import IntegrityError

        from app.factory import db
        from app.models import TiendaNubeProduct

        with pg_app.app_context():
            db.session.add(TiendaNubeProduct(tn_id=1, name="a"))
            db.session.commit()
            db.session.add(TiendaNubeProduct(tn_id=1, name="b"))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_editable_copy_round_trips(self, pg_app) -> None:
        """site_texts is the one table with irreplaceable data — the admin's edits."""
        from app.content import REGISTRY

        key = REGISTRY.groups[0].sections[0].fields[0].key
        with pg_app.app_context():
            from sitecopy.state import current_store

            store = current_store()
            store.set_published(key, "Texto editado")
            store.commit()

        # A second app context, so the value is read back from Postgres rather than
        # from the session that wrote it.
        with pg_app.app_context():
            from sitecopy.state import current_store

            assert current_store().get(key).published_value == "Texto editado"


# --- the app, end to end ------------------------------------------------------


class TestServesRequests:
    def test_home_renders(self, pg_app) -> None:
        response = pg_app.test_client().get("/")

        assert response.status_code == 200

    def test_catalog_sync_populates_the_mirror(self, pg_app, monkeypatch) -> None:
        from app.models import TiendaNubeProduct
        from app.services import catalog_sync

        class FakeClient:
            def iter_products(self, **_kwargs):  # noqa: ANN003
                return iter(
                    [
                        {
                            "id": 11,
                            "name": {"es": "Bolso"},
                            "published": True,
                            "variants": [{"id": 110, "price": "1000.00", "stock": 2}],
                            "images": [{"src": "https://example.test/a.jpg"}],
                        }
                    ]
                )

        with pg_app.app_context():
            result = catalog_sync.sync_products(FakeClient())

            assert result.created == 1
            assert TiendaNubeProduct.query.count() == 1
