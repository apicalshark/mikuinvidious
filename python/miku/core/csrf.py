# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import hmac
import os
from functools import wraps

from quart import request, session

CSRF_TOKEN_KEY = "csrf_token"
CSRF_TOKEN_TTL = 3600  # 1 hour


def generate_csrf_token() -> str:
    """Generate a new CSRF token."""
    return os.urandom(32).hex()


async def get_csrf_token() -> str:
    """Get or create CSRF token for current session."""
    if CSRF_TOKEN_KEY not in session:
        session[CSRF_TOKEN_KEY] = generate_csrf_token()
    return session[CSRF_TOKEN_KEY]


async def validate_csrf_token(token: str) -> bool:
    """Validate CSRF token from form/header against session."""
    if not token:
        return False
    session_token = session.get(CSRF_TOKEN_KEY)
    if not session_token:
        return False
    return hmac.compare_digest(session_token, token)


def csrf_protect():
    """Decorator to protect endpoints with CSRF validation."""

    def decorator(f):
        @wraps(f)
        async def wrapped(*args, **kwargs):
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                # Check header first (for AJAX), then form
                token = request.headers.get("X-CSRF-Token")
                if not token and request.mimetype in ("application/x-www-form-urlencoded", "multipart/form-data"):
                    form = await request.form
                    token = form.get("csrf_token")

                if not await validate_csrf_token(token):
                    from quart import Response

                    return Response("CSRF token validation failed", status=403)
            return await f(*args, **kwargs)

        return wrapped

    return decorator


async def inject_csrf_token():
    """Template context processor to inject CSRF token."""
    token = await get_csrf_token()
    return {"csrf_token": token}
