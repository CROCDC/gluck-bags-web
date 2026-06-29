"""Single shared-password auth for the admin panel."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import current_app, redirect, session, url_for
from werkzeug.wrappers import Response

F = TypeVar("F", bound=Callable[..., Any])

SESSION_KEY = "admin"


def is_logged_in() -> bool:
    return bool(session.get(SESSION_KEY))


def check_password(password: str) -> bool:
    """Constant-time compare against ADMIN_PASSWORD (False if it's unset)."""
    expected = current_app.config.get("ADMIN_PASSWORD") or ""
    if not expected:
        return False
    return hmac.compare_digest(password, expected)


def login() -> None:
    session[SESSION_KEY] = True


def logout() -> None:
    session.pop(SESSION_KEY, None)


def login_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not is_logged_in():
            return cast(Response, redirect(url_for("admin.login")))
        return view(*args, **kwargs)

    return cast(F, wrapped)
