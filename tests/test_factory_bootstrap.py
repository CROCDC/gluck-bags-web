"""Boot-time bootstrapping: AUTO_INIT_DB, `flask init-db`, and the SECRET_KEY guard.

Dimension: factory_bootstrap. Creating tables, seeding and healing media happens in the
app factory, which is right for a container that boots once against a volume and wrong
for a serverless deploy, where every cold start would redo it inside the request that
woke the instance. These tests pin the flag that separates the two, and the command that
replaces the boot-time work.

They also pin the SECRET_KEY failure mode, which is the subtle one: without a writable
DATA_DIR the old code silently minted a per-process key, so the site booted green and
logged the admin out on every cold start.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import inspect


def _env(monkeypatch, tmp_path, **overrides: str) -> None:
    """A clean, isolated environment for building a fresh app."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)


def _table_names(application) -> set[str]:
    from app.factory import db

    with application.app_context():
        return set(inspect(db.engine).get_table_names())


# --- AUTO_INIT_DB -------------------------------------------------------------


class TestAutoInitDb:
    def test_on_by_default_so_local_and_tests_bootstrap_themselves(
        self, tmp_path, monkeypatch
    ) -> None:
        _env(monkeypatch, tmp_path)
        monkeypatch.delenv("AUTO_INIT_DB", raising=False)
        from app.factory import create_app

        application = create_app()

        assert application.config["AUTO_INIT_DB"] is True
        assert "products" in _table_names(application)

    @pytest.mark.parametrize("value", ["0", "false", "False", ""])
    def test_disabled_values_skip_schema_creation(
        self, tmp_path, monkeypatch, value: str
    ) -> None:
        """The whole point: no create_all, no seed, no media backfill in the boot path."""
        _env(monkeypatch, tmp_path, AUTO_INIT_DB=value)
        from app.factory import create_app

        application = create_app()

        assert application.config["AUTO_INIT_DB"] is False
        assert _table_names(application) == set()

    def test_sitecopy_tables_are_skipped_too(self, tmp_path, monkeypatch) -> None:
        """flask-sitecopy owns `site_texts` and creates it from `register_content`, not
        from `_initialize_schema` — a second, easily-missed source of boot-time DDL."""
        _env(monkeypatch, tmp_path, AUTO_INIT_DB="0")
        from app.factory import create_app

        application = create_app()

        assert "site_texts" not in _table_names(application)

    def test_app_still_builds_and_serves_without_init(self, tmp_path, monkeypatch) -> None:
        """Registering routes/blueprints must not depend on the tables existing, or a
        serverless boot would 500 before `flask init-db` ever runs."""
        _env(monkeypatch, tmp_path, AUTO_INIT_DB="0")
        from app.factory import create_app

        application = create_app()

        assert application.url_map.bind("localhost").match("/") is not None


# --- flask init-db ------------------------------------------------------------


class TestInitDbCommand:
    def test_creates_the_schema_that_boot_skipped(self, tmp_path, monkeypatch) -> None:
        _env(monkeypatch, tmp_path, AUTO_INIT_DB="0")
        from app.factory import create_app

        application = create_app()
        assert _table_names(application) == set()

        result = application.test_cli_runner().invoke(args=["init-db"])

        assert result.exit_code == 0, result.output
        tables = _table_names(application)
        assert "products" in tables
        assert "product_media" in tables
        assert "tiendanube_products" in tables
        # sitecopy's, which _initialize_schema alone does not create.
        assert "site_texts" in tables

    def test_is_idempotent(self, tmp_path, monkeypatch) -> None:
        """It runs on every deploy, so a second run must be a no-op, not an error."""
        _env(monkeypatch, tmp_path, AUTO_INIT_DB="0")
        from app.factory import create_app

        application = create_app()
        runner = application.test_cli_runner()

        first = runner.invoke(args=["init-db"])
        second = runner.invoke(args=["init-db"])

        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        assert "products" in _table_names(application)

    def test_seeds_when_seeding_is_enabled(self, tmp_path, monkeypatch) -> None:
        _env(monkeypatch, tmp_path, AUTO_INIT_DB="0", SEED_PRODUCTS="1")
        from app.factory import create_app
        from app.models import Product

        application = create_app()
        application.test_cli_runner().invoke(args=["init-db"])

        with application.app_context():
            assert Product.query.count() > 0


# --- SECRET_KEY ---------------------------------------------------------------


class TestSecretKeyGuard:
    def test_env_value_wins(self, tmp_path) -> None:
        from app.factory import _resolve_secret_key

        os.environ["SECRET_KEY"] = "from-the-env"
        try:
            assert _resolve_secret_key(str(tmp_path)) == "from-the-env"
        finally:
            del os.environ["SECRET_KEY"]

    def test_persists_a_generated_key_when_the_dir_is_writable(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
        from app.factory import _resolve_secret_key

        key = _resolve_secret_key(str(tmp_path))

        assert key
        assert (tmp_path / "secret_key").read_text(encoding="utf-8").strip() == key

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores the read-only bit",
    )
    def test_raises_instead_of_minting_a_throwaway_key(self, tmp_path, monkeypatch) -> None:
        """On a read-only filesystem there is nowhere to persist a key. Booting anyway
        means a new random key per process, which invalidates every admin session on
        each cold start — a much harder failure to read than refusing to start."""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        read_only = tmp_path / "ro"
        read_only.mkdir()
        read_only.chmod(0o500)
        from app.factory import _resolve_secret_key

        try:
            with pytest.raises(RuntimeError, match="SECRET_KEY"):
                _resolve_secret_key(str(read_only))
        finally:
            read_only.chmod(0o700)
