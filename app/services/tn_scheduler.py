"""Hourly Tienda Nube mirror sync (headless POC — production wiring).

Keeps the local mirror (`TiendaNubeProduct`) fresh so the storefront and cart serve
current data even between webhook deliveries (and reconciles anything a webhook
missed). Two entry points share one core (`run_sync`):

- **Scheduler** (`start_scheduler`): a daemon thread started from the app factory. It
  runs only when Tienda Nube credentials are configured, so dev/test (no token) never
  spawn it. With gunicorn's multiple workers this thread runs in each, but a
  cross-process file lock (same `fcntl` trick as the schema init) means only ONE
  worker syncs at a time, and a persisted timestamp means it syncs at most once per
  interval — so N workers still produce one hourly sync, and a worker dying just hands
  off to another.
- **CLI** (`flask sync-tn`): a manual/forced sync, handy for the first population and
  for an external cron if that's ever preferred over the in-process scheduler.
- **HTTP** (`GET /internal/sync-tn`, see app/tiendanube/): the same forced sync behind a
  shared secret, for a scheduler that lives outside the process. That is the only option
  where no process survives between requests (serverless), and it is driven by a GitHub
  Actions schedule so the cadence stays in version control rather than in a host's
  dashboard.

The core never raises into its callers: a sync failure logs nothing louder than a
skipped return, so a transient API hiccup can't crash a worker or the CLI.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from app.services import catalog_sync
from app.services.tiendanube_client import TiendaNubeClient

DEFAULT_INTERVAL_SECONDS = 3600  # hourly
# How often the thread wakes to check whether an interval has elapsed. Small so the
# hourly boundary and process shutdown are both honoured promptly.
_CHECK_EVERY_SECONDS = 300

_STAMP_FILE = "tn_sync.stamp"
_LOCK_FILE = ".tn_sync.lock"

_thread: Optional[threading.Thread] = None
_stop = threading.Event()


# --- core --------------------------------------------------------------------


def _build_client() -> Optional[TiendaNubeClient]:
    try:
        return TiendaNubeClient.from_env()
    except ValueError:
        return None


def _stamp_path(app: Flask) -> str:
    return os.path.join(app.config["DATA_DIR"], _STAMP_FILE)


def _seconds_since_last(app: Flask) -> Optional[float]:
    """Seconds since the last successful sync, or None if it never ran."""
    try:
        with open(_stamp_path(app), encoding="utf-8") as fh:
            return max(0.0, time.time() - float(fh.read().strip()))
    except (OSError, ValueError):
        return None


def _touch_stamp(app: Flask) -> None:
    try:
        with open(_stamp_path(app), "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass


def _open_lock_file(app: Flask) -> Optional[Any]:
    """The lock file handle, or None when this platform can't have one."""
    try:
        import fcntl  # noqa: F401 — presence is the thing being tested
    except ImportError:
        return None
    try:
        return open(os.path.join(app.config["DATA_DIR"], _LOCK_FILE), "w")
    except OSError:
        return None


@contextmanager
def _sync_lock(app: Flask) -> Iterator[bool]:
    """Yield whether this caller may sync.

    The lock is here for gunicorn's workers: each runs its own scheduler thread against
    one shared DATA_DIR, and only one of them should sync. Two situations look identical
    to `open`/`flock` but mean opposite things — another worker HOLDING the lock means
    skip, while no lock being *possible* (no fcntl, or a read-only DATA_DIR, which is
    what a serverless deploy has) means there is no second worker to race, so proceed.
    Treating both as "skip" made every sync a silent no-op exactly where the cron
    endpoint is the only caller.
    """
    handle = _open_lock_file(app)
    if handle is None:
        yield True
        return

    import fcntl

    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def is_configured() -> bool:
    """Whether Tienda Nube credentials are present, so a caller can tell a real
    misconfiguration apart from a sync that was merely skipped."""
    return _build_client() is not None


def run_sync(
    app: Flask,
    *,
    client: Optional[TiendaNubeClient] = None,
    force: bool = False,
    interval: int = DEFAULT_INTERVAL_SECONDS,
) -> Optional[catalog_sync.SyncResult]:
    """Sync the mirror if due (or `force`). Returns the SyncResult, or None when
    skipped (no credentials, another worker holds the lock, or not yet due)."""
    client = client or _build_client()
    if client is None:
        return None
    with _sync_lock(app) as may_sync:
        if not may_sync:
            return None
        try:
            if not force:
                elapsed = _seconds_since_last(app)
                if elapsed is not None and elapsed < interval:
                    return None
            with app.app_context():
                result = catalog_sync.sync_products(client)
            _touch_stamp(app)
            return result
        except Exception:  # noqa: BLE001 — a sync must never crash the caller/worker
            return None


# --- scheduler ---------------------------------------------------------------


def start_scheduler(app: Flask) -> None:
    """Start the hourly sync thread once, if TN is configured and not disabled.

    No-op without credentials (dev/test) or when TN_SYNC_ENABLED=0, so importing/
    creating the app stays side-effect-free unless a real token is present."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    if os.environ.get("TN_SYNC_ENABLED", "1") in ("0", "false", "False", ""):
        return
    if _build_client() is None:
        return

    interval = _interval_from_env()
    _stop.clear()

    def _loop() -> None:
        while not _stop.is_set():
            run_sync(app, interval=interval)
            _stop.wait(min(interval, _CHECK_EVERY_SECONDS))

    _thread = threading.Thread(target=_loop, name="tn-sync", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    """Signal the scheduler thread to stop (used by tests; prod relies on the daemon
    dying with the process)."""
    _stop.set()
    global _thread
    if _thread is not None:
        _thread.join(timeout=2)
        _thread = None


def _interval_from_env() -> int:
    try:
        return max(60, int(os.environ.get("TN_SYNC_INTERVAL", DEFAULT_INTERVAL_SECONDS)))
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS


# --- CLI ---------------------------------------------------------------------


@click.command("sync-tn")
@with_appcontext
def sync_tn_command() -> None:
    """Force a full Tienda Nube mirror sync now (manual / cron)."""
    result = run_sync(current_app._get_current_object(), force=True)
    if result is None:
        click.echo(
            "Sync omitido: faltan credenciales (TN_STORE_ID / TN_ACCESS_TOKEN) "
            "o no se pudo tomar el lock."
        )
        return
    click.echo(f"Sync OK — {result}")


def register_cli(app: Flask) -> None:
    app.cli.add_command(sync_tn_command)
