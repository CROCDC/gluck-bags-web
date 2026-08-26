#!/bin/sh
# Container entrypoint: take a safety backup of the SQLite DB, then start the app.
#
# The backup is best-effort and MUST NEVER stop the app from starting — `|| true`
# guarantees a backup problem can't cause an outage. It runs once per container
# start (not per gunicorn worker), so each deploy leaves a fresh snapshot under
# $DATA_DIR/backups before the new code serves a request. See scripts/backup_db.py.
set -u

python /app/scripts/backup_db.py || true

# Hand off to the CMD (gunicorn). `exec` so signals reach gunicorn directly.
exec "$@"
