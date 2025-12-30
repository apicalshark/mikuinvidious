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
from shared import app, appconf, close_global_client


async def main():
    # Register shutdown hook
    app.after_serving(close_global_client)

    config = Config()
    # Bind to configured host and port to allow cross-container communication
    host = appconf["server"]["host"]
    port = appconf["server"]["port"]
    config.bind = [f"{host}:{port}"]
    config.accesslog = None
    config.errorlog = "-"
    config.keep_alive_timeout = 30
    config.tcp_keepalive = True
    config.response_timeout = 10800  # 3 hours for streaming

    sys.stderr.write(f"Starting MikuInvidious (ASGI) on {config.bind[0]}\n")
    sys.stderr.write(f"Hypercorn Config: Keep-Alive={config.keep_alive_timeout}, Response={config.response_timeout}\n")
    sys.stderr.flush()
    await serve(app, config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
