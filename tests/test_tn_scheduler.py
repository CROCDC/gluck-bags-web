"""Tests for the hourly mirror sync (app.services.tn_scheduler).

Exercises the sync core (due/not-due/forced, credential gating), the scheduler
thread's start gating, and the `flask sync-tn` command — all with a fake client, so
nothing hits the network. TN credentials are neutralized by conftest, so the
"no credentials" branches are the real default here.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from app.models import TiendaNubeProduct
from app.services import tn_scheduler

if TYPE_CHECKING:
    from flask import Flask


class FakeClient:
    def __init__(self, products: list[dict[str, Any]]) -> None:
        self._products = products

    def iter_products(self, **_kwargs: Any):  # noqa: ANN003
        return iter(self._products)


def _payload(tn_id: int) -> dict[str, Any]:
    return {
        "id": tn_id,
        "name": {"es": f"Bolso {tn_id}"},
        "published": True,
        "variants": [{"id": tn_id * 10, "price": "1000.00", "stock": 3}],
        "images": [],
    }


# --- run_sync ----------------------------------------------------------------


def test_run_sync_populates_mirror_when_forced(app: "Flask") -> None:
    client = FakeClient([_payload(1), _payload(2)])
    result = tn_scheduler.run_sync(app, client=client, force=True)
    assert result is not None
    assert result.created == 2
    with app.app_context():
        assert TiendaNubeProduct.query.count() == 2


def test_run_sync_skips_when_not_due(app: "Flask") -> None:
    tn_scheduler.run_sync(app, client=FakeClient([_payload(1)]), force=True)  # writes stamp
    # A second, non-forced run within the interval is a no-op.
    again = tn_scheduler.run_sync(app, client=FakeClient([_payload(1), _payload(2)]), force=False)
    assert again is None
    with app.app_context():
        assert TiendaNubeProduct.query.count() == 1  # unchanged


def test_run_sync_runs_when_interval_elapsed(app: "Flask") -> None:
    tn_scheduler.run_sync(app, client=FakeClient([_payload(1)]), force=True)
    # interval=0 => always due, even without forcing.
    again = tn_scheduler.run_sync(app, client=FakeClient([_payload(1), _payload(2)]), interval=0)
    assert again is not None
    with app.app_context():
        assert TiendaNubeProduct.query.count() == 2


def test_run_sync_without_credentials_returns_none(app: "Flask") -> None:
    # conftest neutralizes TN_* -> _build_client() is None -> skip.
    assert tn_scheduler.run_sync(app, force=True) is None


# --- scheduler start gating --------------------------------------------------


def test_scheduler_does_not_start_without_credentials(app: "Flask") -> None:
    tn_scheduler._thread = None
    tn_scheduler.start_scheduler(app)
    assert tn_scheduler._thread is None  # no token -> no thread


def test_scheduler_starts_when_configured(app: "Flask", monkeypatch) -> None:
    monkeypatch.setattr(tn_scheduler, "_build_client", lambda: object())
    ran = threading.Event()
    monkeypatch.setattr(tn_scheduler, "run_sync", lambda *a, **k: ran.set())
    tn_scheduler._thread = None
    try:
        tn_scheduler.start_scheduler(app)
        assert tn_scheduler._thread is not None
        assert ran.wait(2), "scheduler thread never called run_sync"
    finally:
        tn_scheduler.stop_scheduler()
    assert tn_scheduler._thread is None


def test_scheduler_respects_disable_flag(app: "Flask", monkeypatch) -> None:
    monkeypatch.setattr(tn_scheduler, "_build_client", lambda: object())
    monkeypatch.setenv("TN_SYNC_ENABLED", "0")
    tn_scheduler._thread = None
    tn_scheduler.start_scheduler(app)
    assert tn_scheduler._thread is None


# --- CLI ---------------------------------------------------------------------


def test_cli_reports_missing_credentials(app: "Flask") -> None:
    result = app.test_cli_runner().invoke(tn_scheduler.sync_tn_command)
    assert result.exit_code == 0
    assert "omitido" in result.output.lower()


def test_cli_runs_sync_with_client(app: "Flask", monkeypatch) -> None:
    monkeypatch.setattr(tn_scheduler, "_build_client", lambda: FakeClient([_payload(1), _payload(2)]))
    result = app.test_cli_runner().invoke(tn_scheduler.sync_tn_command)
    assert result.exit_code == 0
    assert "Sync OK" in result.output
    with app.app_context():
        assert TiendaNubeProduct.query.count() == 2
