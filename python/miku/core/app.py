# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.
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
        app.config["BODY_TIMEOUT"] = 10800
    configured_secret = appconf.get("server", {}).get("secret_key")
    if not configured_secret and not app.debug:
        print(
            "[Security] Warning: server.secret_key / QUART_SECRET_KEY is unset; "
            "generating an ephemeral key (CSRF sessions will reset on restart)."
        )
    app.secret_key = configured_secret or os.urandom(32).hex()

    app.session_interface = SecureCookieSessionInterface()
    if "SESSION_COOKIE_SAMESITE" not in appconf.get("quart", {}):
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if "SESSION_COOKIE_SECURE" not in appconf.get("quart", {}):
        site_url = str(appconf.get("site", {}).get("site_url", "")).lower()
        app.config["SESSION_COOKIE_SECURE"] = site_url.startswith("https://")

    return app
