"""The one-shot data migration: scripts/migrate_sqlite_to_postgres.py.

Dimension: migration. This script runs ONCE, against production data, and `site_texts`
(the copy edited from the admin) is the only table in the app that cannot be rebuilt
from somewhere else. So the things worth pinning are the ones that lose or corrupt data
silently:

- SQLite has no JSON type and no boolean type; both are stand-ins that Postgres rejects
  or stores wrong;
- re-running the script must not duplicate rows;
- the id sequences have to be moved past the copied rows, or the first product created
  after the cutover collides with an existing id.

Skipped unless TEST_DATABASE_URL is set — same requirement as tests/test_postgres.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

RAW_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "migrate_sqlite_to_postgres.py"

pytestmark = pytest.mark.skipif(
    not RAW_URL, reason="TEST_DATABASE_URL not set; see this module's docstring"
)


@pytest.fixture
def source_db(tmp_path, monkeypatch):
    """A SQLite database shaped like production: products with media, a TN mirror row
    with real nested JSON, and an edited copy override."""
    data_dir = tmp_path / "source"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTO_INIT_DB", "1")

    from app.factory import create_app, db
    from app.models import Media, Product, TiendaNubeProduct

    application = create_app()
    with application.app_context():
        product = Product(title="Bolso migrado", price=45000, is_published=True)
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
        db.session.add(
            TiendaNubeProduct(
                tn_id=99,
                name="Bolso TN",
                published=True,
                variants=[{"id": 990, "price": "1000.00", "stock": 3}],
                images=["https://example.test/a.jpg"],
                raw={"id": 99, "name": {"es": "Bolso TN"}, "tags": ["x"]},
            )
        )
        db.session.commit()

        from sitecopy.state import current_store

        store = current_store()
        store.set_published("__migration_probe__", "Copia editada")
        store.commit()

        db.session.remove()
        db.engine.dispose()

    return str(data_dir / "gluck.db")


@pytest.fixture
def target_schema():
    """An empty Postgres schema with the app's tables already created."""
    import sqlalchemy

    from app.factory import _normalize_database_url

    schema = f"m_{uuid.uuid4().hex[:16]}"
    engine = sqlalchemy.create_engine(_normalize_database_url(RAW_URL))
    with engine.begin() as conn:
        conn.execute(sqlalchemy.schema.CreateSchema(schema))
    engine.dispose()

    yield schema

    engine = sqlalchemy.create_engine(_normalize_database_url(RAW_URL))
    with engine.begin() as conn:
        conn.execute(sqlalchemy.schema.DropSchema(schema, cascade=True))
    engine.dispose()


def _scoped_url(schema: str) -> str:
    return f"{RAW_URL}?options=-csearch_path%3D{schema}"


def _init_target(schema: str, tmp_path) -> None:
    """`flask init-db` against the target schema — the documented prerequisite."""
    env = dict(os.environ)
    env.update(
        DATABASE_URL=_scoped_url(schema),
        DATA_DIR=str(tmp_path / "target-data"),
        SEED_PRODUCTS="0",
        ADMIN_PASSWORD="x",
        SECRET_KEY="test-secret-key",
        AUTO_INIT_DB="0",
        FLASK_APP="run.py",
    )
    result = subprocess.run(
        [sys.executable, "-m", "flask", "init-db"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _migrate(source: str, schema: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sqlite",
            source,
            "--postgres",
            _scoped_url(schema),
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _query(schema: str, sql: str):
    import sqlalchemy

    from app.factory import _normalize_database_url

    engine = sqlalchemy.create_engine(_normalize_database_url(_scoped_url(schema)))
    try:
        with engine.begin() as conn:
            return conn.execute(sqlalchemy.text(sql)).fetchall()
    finally:
        engine.dispose()


# --- happy path ---------------------------------------------------------------


class TestMigration:
    def test_copies_every_table(self, source_db, target_schema, tmp_path) -> None:
        _init_target(target_schema, tmp_path)

        result = _migrate(source_db, target_schema)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _query(target_schema, "select count(*) from products")[0][0] == 1
        assert _query(target_schema, "select count(*) from product_media")[0][0] == 1
        assert _query(target_schema, "select count(*) from tiendanube_products")[0][0] == 1

    def test_json_columns_arrive_as_json_not_as_a_string(
        self, source_db, target_schema, tmp_path
    ) -> None:
        """SQLite stores these as TEXT. Inserting that string into a json column either
        errors or stores a quoted string that every later read misparses."""
        _init_target(target_schema, tmp_path)
        _migrate(source_db, target_schema)

        rows = _query(
            target_schema,
            "select raw -> 'name' ->> 'es', variants -> 0 ->> 'stock', "
            "jsonb_array_length(to_jsonb(images)) from tiendanube_products",
        )

        assert rows[0][0] == "Bolso TN"
        assert rows[0][1] == "3"
        assert rows[0][2] == 1

    def test_widths_survive_as_a_list(self, source_db, target_schema, tmp_path) -> None:
        _init_target(target_schema, tmp_path)
        _migrate(source_db, target_schema)

        rows = _query(target_schema, "select widths from product_media")

        assert json.loads(json.dumps(rows[0][0])) == [400, 600, 1000]

    def test_booleans_are_real_booleans(self, source_db, target_schema, tmp_path) -> None:
        """SQLite writes 0/1 integers; Postgres declares these columns boolean."""
        _init_target(target_schema, tmp_path)
        _migrate(source_db, target_schema)

        assert _query(target_schema, "select is_published from products")[0][0] is True
        assert _query(target_schema, "select is_cover from product_media")[0][0] is True

    def test_edited_copy_survives(self, source_db, target_schema, tmp_path) -> None:
        """site_texts is the only table here that cannot be rebuilt from anywhere else."""
        _init_target(target_schema, tmp_path)
        _migrate(source_db, target_schema)

        rows = _query(
            target_schema,
            "select published_value from site_texts where key = '__migration_probe__'",
        )

        assert rows[0][0] == "Copia editada"

    def test_id_sequences_move_past_the_copied_rows(
        self, source_db, target_schema, tmp_path
    ) -> None:
        """Rows arrive with their SQLite ids while the sequence still sits at 1, so the
        first product created after the cutover collides with an existing id."""
        _init_target(target_schema, tmp_path)
        _migrate(source_db, target_schema)

        # The defaults live on the model, not on the table, so a raw INSERT has to
        # supply every NOT NULL column itself.
        rows = _query(
            target_schema,
            "insert into products (title, price, currency, is_published, position, "
            "created_at, updated_at) values ('Nuevo', 1000, 'ARS', true, 0, now(), now()) "
            "returning id",
        )
        existing = _query(target_schema, "select max(id) from products")[0][0]

        assert rows[0][0] == existing
        # And it must not have reused the id the migrated row already holds.
        assert rows[0][0] > 1


# --- guard rails --------------------------------------------------------------


class TestGuards:
    def test_refuses_to_write_into_a_populated_table(
        self, source_db, target_schema, tmp_path
    ) -> None:
        """Re-running must not silently double site_texts."""
        _init_target(target_schema, tmp_path)
        _migrate(source_db, target_schema)

        second = _migrate(source_db, target_schema)

        assert second.returncode == 1
        assert "--truncate" in second.stderr
        assert _query(target_schema, "select count(*) from products")[0][0] == 1

    def test_truncate_replaces_instead_of_appending(
        self, source_db, target_schema, tmp_path
    ) -> None:
        _init_target(target_schema, tmp_path)
        _migrate(source_db, target_schema)

        result = _migrate(source_db, target_schema, "--truncate")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _query(target_schema, "select count(*) from products")[0][0] == 1

    def test_dry_run_writes_nothing(self, source_db, target_schema, tmp_path) -> None:
        _init_target(target_schema, tmp_path)

        result = _migrate(source_db, target_schema, "--dry-run")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "copiaría" in result.stdout
        assert _query(target_schema, "select count(*) from products")[0][0] == 0

    def test_fails_clearly_when_the_target_has_no_schema(
        self, source_db, target_schema
    ) -> None:
        """Running it before `flask init-db` must say so, not raise a driver error."""
        result = _migrate(source_db, target_schema)

        assert result.returncode == 1
        assert "init-db" in result.stderr

    def test_fails_clearly_on_a_missing_sqlite_file(self, target_schema) -> None:
        result = _migrate("/nonexistent/gluck.db", target_schema)

        assert result.returncode == 1
        assert "No existe" in result.stderr
