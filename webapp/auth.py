"""HTTP Basic Auth gated by ADMIN_USERNAME / ADMIN_PASSWORD env vars.

Constant-time credential comparison via hmac.compare_digest. If
ADMIN_PASSWORD is empty, the app refuses to start in non-localhost mode —
this prevents accidental wide-open deployment.
"""
from __future__ import annotations

import hmac
from functools import wraps
from typing import Callable

from flask import Response, current_app, request


def _credentials_match(username: str, password: str) -> bool:
    cfg = current_app.config
    expected_user = cfg.get("ADMIN_USERNAME", "")
    expected_pass = cfg.get("ADMIN_PASSWORD", "")
    if not expected_pass:
        # Configured passwordless — only allowed on localhost (enforced in app factory)
        return True
    user_ok = hmac.compare_digest(username or "", expected_user)
    pass_ok = hmac.compare_digest(password or "", expected_pass)
    return user_ok and pass_ok


def requires_auth(f: Callable) -> Callable:
    @wraps(f)
    def wrapped(*args, **kwargs):
        auth = request.authorization
        if not auth or not _credentials_match(auth.username, auth.password):
            return Response(
                "Authentication required.", 401,
                {"WWW-Authenticate": 'Basic realm="XAUUSD Bot", charset="UTF-8"'},
            )
        return f(*args, **kwargs)
    return wrapped
