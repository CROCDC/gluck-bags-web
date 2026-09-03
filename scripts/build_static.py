"""Publish `app/static/` to `public/static/` and write the cache-busting manifest.

Run as the build step of a deploy that serves static files from a CDN instead of from
the app. Two outputs:

- `public/static/**` — Vercel serves everything under `public/` straight from the CDN,
  at the same `/static/...` paths `url_for('static', ...)` already generates. Without
  this, every stylesheet, font and video request wakes the Python function.
- `app/static_manifest.json` — `filename -> content hash`, read once at boot by the
  cache-buster in `app/factory.py`.

The manifest exists because the buster used to `os.stat` the file it was versioning.
Once the CDN owns those files they may not be in the function bundle at all, so the stat
would fail, the `?v=` would silently disappear, and `SEND_FILE_MAX_AGE_DEFAULT` (one
year) would pin every client to the pre-deploy asset. A content hash is also better than
an mtime here: a rebuild that does not change a file leaves its URL alone, so the CDN and
every browser keep their copy.

    python scripts/build_static.py

Idempotent: it rebuilds `public/static` from scratch each time.

This is a DEPLOY step, not a dev one. While `app/static_manifest.json` exists the app
versions static URLs by content hash, so an edited stylesheet keeps its old URL until
the build runs again. Delete that file to go back to the mtime-based buster.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE = os.path.join(REPO_ROOT, "app", "static")
DESTINATION = os.path.join(REPO_ROOT, "public", "static")
MANIFEST = os.path.join(REPO_ROOT, "app", "static_manifest.json")

# Fonts are deliberately unversioned: they are also referenced from CSS `url()` with no
# `?v=`, so a versioned <link rel=preload> would point at a different URL than the
# @font-face rule — the preload would be wasted and the font fetched twice, delaying it
# and, with font-display:swap, the LCP. Kept in sync with app/factory.py.
UNVERSIONED_EXTENSIONS = {"woff2", "woff", "ttf", "otf", "eot"}

# Never publish these to a public CDN. .DS_Store in particular leaks the directory
# listing of whatever machine ran the build.
IGNORED = shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc", "Thumbs.db", "*.swp")


def _is_ignored(relative: str) -> bool:
    return any(
        part in (".DS_Store", "__pycache__", "Thumbs.db") or part.endswith((".pyc", ".swp"))
        for part in relative.split("/")
    )


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def build() -> dict[str, str]:
    if not os.path.isdir(SOURCE):
        raise SystemExit(f"No existe el directorio de estáticos: {SOURCE}")

    shutil.rmtree(DESTINATION, ignore_errors=True)
    shutil.copytree(SOURCE, DESTINATION, ignore=IGNORED)

    manifest: dict[str, str] = {}
    for directory, _subdirs, filenames in os.walk(SOURCE):
        for filename in filenames:
            absolute = os.path.join(directory, filename)
            # The key is exactly what `url_for('static', filename=...)` is given, so
            # always "/" separated regardless of the platform building this.
            relative = os.path.relpath(absolute, SOURCE).replace(os.sep, "/")
            if _is_ignored(relative):
                continue
            if relative.rsplit(".", 1)[-1].lower() in UNVERSIONED_EXTENSIONS:
                continue
            manifest[relative] = _hash_file(absolute)

    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return manifest


def main() -> int:
    manifest = build()
    total = sum(
        os.path.getsize(os.path.join(d, f))
        for d, _s, files in os.walk(DESTINATION)
        for f in files
    )
    print(
        f"Estáticos publicados en public/static ({total / 1_048_576:.1f} MB), "
        f"{len(manifest)} archivos versionados en app/static_manifest.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
