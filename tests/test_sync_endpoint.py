"""The out-of-process sync trigger: `GET /internal/sync-tn`.

Dimension: sync_endpoint. The hourly mirror sync ran in a daemon thread inside gunicorn,
which needs a process that outlives the request. This endpoint is the same forced sync
behind a shared secret, so a scheduler outside the process (GitHub Actions, a host's
cron) can drive it.

It mutates the whole catalogue mirror, so the auth tests here are the point: an
unconfigured or wrongly-called deploy must never resync from an anonymous request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.models import TiendaNubeProduct
from app.services import tn_scheduler

if TYPE_CHECKING:
    from flask import Flask

SECRET = "cron-secret-value"
URL = "/internal/sync-tn"


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


@pytest.fixture
def configured(monkeypatch):
    """CRON_SECRET set and TN credentials present, via a fake client."""
    monkeypatch.setenv("CRON_SECRET", SECRET)
    monkeypatch.setattr(tn_scheduler, "_build_client", lambda: FakeClient([_payload(1)]))


def _auth(secret: str = SECRET) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


# --- auth ---------------------------------------------------------------------


class TestAuth:
    def test_404_when_no_secret_is_configured(self, app: "Flask", monkeypatch) -> None:
        """An unconfigured deploy must not expose an anonymous full-resync trigger, nor
        advertise that the route exists at all."""
        monkeypatch.delenv("CRON_SECRET", raising=False)

        assert app.test_client().get(URL).status_code == 404

    def test_401_without_an_authorization_header(self, app: "Flask", configured) -> None:
        assert app.test_client().get(URL).status_code == 401

    @pytest.mark.parametrize(
        "header",
        [
            "Bearer wrong-secret",
            "Bearer ",
            SECRET,  # right value, missing the scheme
            "Basic " + SECRET,
            "bearer " + SECRET,  # scheme is compared verbatim
        ],
    )
    def test_401_on_a_bad_authorization_header(
        self, app: "Flask", configured, header: str
    ) -> None:
        response = app.test_client().get(URL, headers={"Authorization": header})

        assert response.status_code == 401

    def test_non_ascii_header_is_rejected_not_crashed(self, app: "Flask", configured) -> None:
        """compare_digest raises TypeError on a non-ASCII str; the header is attacker
        controlled, so a 500 here would be a trivially triggerable denial of service."""
        response = app.test_client().get(URL, headers={"Authorization": "Bearer ñoño"})

        assert response.status_code == 401

    def test_a_bad_call_does_not_touch_the_mirror(self, app: "Flask", configured) -> None:
        app.test_client().get(URL, headers={"Authorization": "Bearer nope"})

        with app.app_context():
            assert TiendaNubeProduct.query.count() == 0


# --- behaviour ----------------------------------------------------------------


class TestSync:
    def test_runs_the_sync_and_reports_what_it_did(self, app: "Flask", configured) -> None:
        response = app.test_client().get(URL, headers=_auth())

        assert response.status_code == 200
        assert response.get_json() == {
            "status": "ok",
            "created": 1,
            "updated": 0,
            "pruned": 0,
        }
        with app.app_context():
            assert TiendaNubeProduct.query.count() == 1

    def test_forces_the_sync_ignoring_the_hourly_interval(
        self, app: "Flask", configured
    ) -> None:
        """The caller owns the schedule now, so the endpoint must not second-guess it
        with the in-process interval — two calls in a row both have to sync."""
        client = app.test_client()

        first = client.get(URL, headers=_auth())
        second = client.get(URL, headers=_auth())

        assert first.get_json()["status"] == "ok"
        assert second.get_json()["status"] == "ok"

    def test_503_when_tienda_nube_credentials_are_missing(
        self, app: "Flask", monkeypatch
    ) -> None:
        """A misconfiguration the scheduler has to see: a 200 here would keep the job
        green while the mirror silently went stale."""
        monkeypatch.setenv("CRON_SECRET", SECRET)
        monkeypatch.setattr(tn_scheduler, "_build_client", lambda: None)

        response = app.test_client().get(URL, headers=_auth())

        assert response.status_code == 503
        assert response.get_json()["status"] == "not_configured"

    def test_post_is_accepted_too(self, app: "Flask", configured) -> None:
        response = app.test_client().post(URL, headers=_auth())

        assert response.status_code == 200

    def test_reports_skipped_when_the_sync_yields_nothing(
        self, app: "Flask", monkeypatch
    ) -> None:
        monkeypatch.setenv("CRON_SECRET", SECRET)
        monkeypatch.setattr(tn_scheduler, "_build_client", lambda: FakeClient([]))
        monkeypatch.setattr(tn_scheduler, "run_sync", lambda *a, **k: None)

        response = app.test_client().get(URL, headers=_auth())

        assert response.status_code == 200
        assert response.get_json()["status"] == "skipped"


# --- the lock, which is what made this endpoint possible ----------------------


class TestSyncLock:
    def test_proceeds_when_no_lock_file_can_be_created(
        self, app: "Flask", monkeypatch
    ) -> None:
        """A read-only DATA_DIR (every serverless deploy) means no lock file AND no
        second worker to race. The old code collapsed both into "skip", which turned
        every sync into a silent no-op exactly where this endpoint is the only caller."""
        monkeypatch.setattr(tn_scheduler, "_open_lock_file", lambda _app: None)

        with tn_scheduler._sync_lock(app) as may_sync:
            assert may_sync is True

    def test_skips_while_another_holder_has_the_lock(self, app: "Flask") -> None:
        """The reason the lock exists: gunicorn's workers must not sync concurrently."""
        with tn_scheduler._sync_lock(app) as first:
            assert first is True
            with tn_scheduler._sync_lock(app) as second:
                assert second is False

    def test_the_lock_is_released_afterwards(self, app: "Flask") -> None:
        with tn_scheduler._sync_lock(app) as first:
            assert first is True

        with tn_scheduler._sync_lock(app) as again:
            assert again is True

    def test_sync_actually_runs_without_a_lock_file(self, app: "Flask", monkeypatch) -> None:
        """End to end: the endpoint must still sync on a platform with no lock."""
        monkeypatch.setenv("CRON_SECRET", SECRET)
        monkeypatch.setattr(tn_scheduler, "_build_client", lambda: FakeClient([_payload(7)]))
        monkeypatch.setattr(tn_scheduler, "_open_lock_file", lambda _app: None)

        response = app.test_client().get(URL, headers=_auth())

        assert response.get_json()["status"] == "ok"
        with app.app_context():
            assert TiendaNubeProduct.query.count() == 1
