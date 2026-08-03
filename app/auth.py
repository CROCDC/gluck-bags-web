"""Single shared-password auth for the admin panel."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import current_app, jsonify, redirect, request, session, url_for
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


def _wants_json() -> bool:
    """True when the caller is a fetch()/XHR rather than a browser navigation.

    Redirecting one of those to the login PAGE means the caller parses HTML as JSON
    and the user is shown `Unexpected token '<'` instead of "your session expired".
    """
    if request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    # Deliberately NOT `Accept: */*`: an XHR file upload sends that, so inferring from
    # it turned an expired session on the products form from "go log in again" into a
    # dead-end alert.
    accept = request.accept_mimetypes
    return bool(accept["application/json"]) and not accept.accept_html


def login_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not is_logged_in():
            if _wants_json():
                return (
                    jsonify(
                        ok=False,
                        reason="auth",
                        errors=["Se cerró tu sesión. Entrá de nuevo para guardar."],
                    ),
                    401,
                )
            return cast(Response, redirect(url_for("admin.login")))
        return view(*args, **kwargs)

    return cast(F, wrapped)
