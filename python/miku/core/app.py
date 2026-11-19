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

from miku.core.config import load_config


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

    return app
