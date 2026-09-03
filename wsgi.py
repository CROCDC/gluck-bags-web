"""WSGI entrypoint for hosts that import the application instead of running it.

Vercel's Flask preset looks for a top-level `app` in a fixed set of filenames — this is
that file. It stays a one-liner on purpose: `app/__init__.py` already builds the app,
and `run.py` remains the local development runner.
"""

from app import app

__all__ = ["app"]
