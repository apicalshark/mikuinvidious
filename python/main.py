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

import asyncio
import sys

import app as app_module  # noqa: F401
from hypercorn.asyncio import serve
from hypercorn.config import Config
from shared import app, close_global_client


async def main():
    # Register shutdown hook
    app.after_serving(close_global_client)

    config = Config()
    # Bind to 0.0.0.0 to allow cross-container communication
    config.bind = ["0.0.0.0:8080"]
    config.accesslog = "-"
    config.errorlog = "-"
    config.keep_alive_timeout = 10800
    config.response_timeout = None  # Infinite for streaming

    # QUIC (HTTP/3) Support - Default OFF
    import os
    if os.environ.get("ENABLE_HTTP3", "false").lower() == "true":
        cert = os.environ.get("SSL_CERT_PATH")
        key = os.environ.get("SSL_KEY_PATH")
        if cert and key and os.path.exists(cert) and os.path.exists(key):
            config.quic_bind = ["0.0.0.0:8080"]
            config.certfile = cert
            config.keyfile = key
            sys.stderr.write("[Init] QUIC (HTTP/3) enabled on port 8080\n")
        else:
            sys.stderr.write("[Init] QUIC enabled but SSL_CERT_PATH or SSL_KEY_PATH missing/invalid. Falling back to TCP.\n")

    sys.stderr.write(f"Starting MikuInvidious (ASGI) on {config.bind[0]}\n")
    sys.stderr.write(f"Hypercorn Config: Keep-Alive={config.keep_alive_timeout}, Response={config.response_timeout}\n")
    sys.stderr.flush()
    await serve(app, config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
