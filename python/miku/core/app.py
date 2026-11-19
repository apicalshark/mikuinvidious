# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Application factory for MikuInvidious."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask_orjson import OrjsonProvider
from quart import Quart
from quart.sessions import SecureCookieSessionInterface
from werkzeug.exceptions import RequestEntityTooLarge

from miku.core.config import load_config


class _BodySizeLimitMiddleware:
    def __init__(self, asgi_app, app: Quart) -> None:
        self._asgi_app = asgi_app
        self._app = app

    @staticmethod
    async def _send_413(send) -> None:
        body = b"413 Request Entity Too Large"
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self._asgi_app(scope, receive, send)

        max_bytes = self._app.config.get("MAX_CONTENT_LENGTH")
        if not max_bytes or max_bytes <= 0:
            return await self._asgi_app(scope, receive, send)

        for name, value in scope.get("headers", ()):
            if name.lower() == b"content-length":
                try:
                    if int(value) > max_bytes:
                        await self._send_413(send)
                        return None
                except ValueError:
                    pass
                break

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise RequestEntityTooLarge()
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._asgi_app(scope, limited_receive, tracked_send)
        except* RequestEntityTooLarge:
            if not response_started:
                await self._send_413(send)


def create_app(config: dict[str, Any] | None = None) -> Quart:
    """Create and configure a Quart application instance."""
    appconf = config or load_config()
    if config is not None:
        from miku.ui.i18n import set_supported_locales

        set_supported_locales(appconf.get("display", {}).get("supported_locales"))

    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    template_folder = str(root_dir / "templates")
    static_folder = str(root_dir / "static")

    app = Quart(
        "miku",
        template_folder=template_folder,
        static_folder=static_folder,
    )

    app.debug = appconf.get("server", {}).get("debug", False)
    app.json = OrjsonProvider(app)
    quart_conf = appconf.get("quart", {})
    app.config.from_mapping(quart_conf)
    if "RESPONSE_TIMEOUT" not in quart_conf:
        app.config["RESPONSE_TIMEOUT"] = 10800
    if "BODY_TIMEOUT" not in quart_conf:
        app.config["BODY_TIMEOUT"] = 30
    if "MAX_CONTENT_LENGTH" not in quart_conf:
        app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    configured_secret = appconf.get("server", {}).get("secret_key")
    if not configured_secret and not app.debug:
        print(
            "[Security] Warning: server.secret_key / QUART_SECRET_KEY is unset; "
            "generating an ephemeral key (CSRF sessions will reset on restart)."
        )
    app.secret_key = configured_secret or os.urandom(32).hex()

    app.session_interface = SecureCookieSessionInterface()
    if "SESSION_COOKIE_HTTPONLY" not in quart_conf:
        app.config["SESSION_COOKIE_HTTPONLY"] = True
    if "SESSION_COOKIE_SAMESITE" not in quart_conf:
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if "SESSION_COOKIE_SECURE" not in quart_conf:
        site_url = str(appconf.get("site", {}).get("site_url", "")).lower()
        app.config["SESSION_COOKIE_SECURE"] = site_url.startswith("https://")

    app.asgi_app = _BodySizeLimitMiddleware(app.asgi_app, app)

    return app
