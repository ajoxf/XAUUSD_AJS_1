"""HTTP Basic Auth.

Behaviour:
- `ADMIN_PASSWORD` set → every protected route requires the matching
  username + password (constant-time comparison via hmac.compare_digest).
- `ADMIN_PASSWORD` empty AND host is localhost → auth is fully bypassed
  (no prompt). Convenient for personal/single-user setups.
- `ADMIN_PASSWORD` empty AND host is public → the app factory refuses
  to start; see `create_app()`. This prevents a wide-open deployment.
"""
from __future__ import annotations

import hmac
from functools import wraps
from typing import Callable

from flask import Response, current_app, request


def _auth_disabled() -> bool:
    """Auth is disabled when no password is configured. The app factory
    guarantees this only happens on localhost binds."""
    return not current_app.config.get("ADMIN_PASSWORD", "")


def _credentials_match(username: str, password: str) -> bool:
    cfg = current_app.config
    user_ok = hmac.compare_digest(username or "", cfg.get("ADMIN_USERNAME", ""))
    pass_ok = hmac.compare_digest(password or "", cfg.get("ADMIN_PASSWORD", ""))
    return user_ok and pass_ok


def requires_auth(f: Callable) -> Callable:
    @wraps(f)
    def wrapped(*args, **kwargs):
        if _auth_disabled():
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not _credentials_match(auth.username, auth.password):
            return Response(
                "Authentication required.", 401,
                {"WWW-Authenticate": 'Basic realm="XAUUSD Bot", charset="UTF-8"'},
            )
        return f(*args, **kwargs)
    return wrapped
