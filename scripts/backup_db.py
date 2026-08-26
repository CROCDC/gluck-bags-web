#!/usr/bin/env python3
"""Consistent, self-pruning backup of the SQLite database.

Runs from `entrypoint.sh` on every container start (i.e. before each deploy takes
over), so there is always a recent snapshot to roll back to — the DB holds the
products, the media metadata AND the admin-editable copy (`site_texts`).

Why Python and not the `sqlite3` CLI: the stdlib `sqlite3.Connection.backup()` is a
proper ONLINE backup (safe while gunicorn is serving), and needs no extra binary in
the image. It is deliberately best-effort: any failure is logged and swallowed so a
backup problem can never stop the app from starting (entrypoint also `|| true`s it).

Config (env):
  DATA_DIR         where the DB lives (default /data); DB is <DATA_DIR>/gluck.db
  BACKUP_ON_START  "0"/"false"/"no" disables it (default: on)
  BACKUP_KEEP      how many snapshots to retain (default 10)
  BACKUP_DIR       where to write them (default <DATA_DIR>/backups)
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _enabled() -> bool:
    return os.environ.get("BACKUP_ON_START", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _keep() -> int:
    try:
        return max(1, int(os.environ.get("BACKUP_KEEP", "10")))
    except ValueError:
        return 10


def main() -> int:
    if not _enabled():
        print("[backup] BACKUP_ON_START is off — skipping", flush=True)
        return 0

    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    db_path = data_dir / "gluck.db"
    if not db_path.exists():
        print(f"[backup] no DB at {db_path} yet — nothing to back up", flush=True)
        return 0

    backup_dir = Path(os.environ.get("BACKUP_DIR", str(data_dir / "backups")))
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"gluck-{stamp}.db"

    # Online backup: consistent even while workers are writing.
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # Sanity-check the copy (fast on a small DB); warn but keep it if odd.
    try:
        check = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
        try:
            ok = check.execute("PRAGMA quick_check").fetchone()
            rows = check.execute("SELECT count(*) FROM site_texts").fetchone()[0]
        finally:
            check.close()
        status = ok[0] if ok else "unknown"
    except sqlite3.Error as exc:  # a missing site_texts is fine (fresh install)
        status, rows = "ok?", f"n/a ({exc})"

    size_kb = dest.stat().st_size / 1024
    print(
        f"[backup] wrote {dest} ({size_kb:.0f} KB) · integrity={status} · "
        f"site_texts rows={rows}",
        flush=True,
    )

    _prune(backup_dir, _keep())
    return 0


def _prune(backup_dir: Path, keep: int) -> None:
    """Keep only the newest `keep` snapshots (by name — the stamp sorts chronologically)."""
    snapshots = sorted(backup_dir.glob("gluck-*.db"))
    stale = snapshots[:-keep] if len(snapshots) > keep else []
    for old in stale:
        try:
            old.unlink()
            print(f"[backup] pruned old snapshot {old.name}", flush=True)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — a backup failure must never block boot
        print(f"[backup] WARNING: backup failed and was skipped: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(0)
