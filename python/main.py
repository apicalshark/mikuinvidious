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

import sys
from granian import Granian
from granian.constants import Interfaces, Loops, TaskImpl
from granian.http import HTTP1Settings, HTTP2Settings
from shared import appconf

# Import app to ensure it's initialized and hooks are registered
import app as app_module  # noqa: F401


def main():
    # Bind to configured host and port to allow cross-container communication
    host = appconf["server"]["host"]
    port = appconf["server"]["port"]

    # Granian handles the event loop (uvloop) and ASGI interface natively.
    # We use the string target "app:app" to allow potential multi-worker support.
    server = Granian(
        "app:app",
        address=host,
        port=port,
        interface=Interfaces.ASGI,
        loop=Loops.uvloop,
        task_impl=TaskImpl.asyncio,
        http1_settings=HTTP1Settings(
            keep_alive=True,
            header_read_timeout=10,  # 10 seconds
        ),
        http2_settings=HTTP2Settings(
            keep_alive_interval=10000,  # Send ping every 10s
            keep_alive_timeout=5,        # Timeout ping after 5s
        ),
        log_access=True,
    )

    sys.stderr.write(f"Starting MikuInvidious (Granian) on {host}:{port}\n")
    sys.stderr.flush()

    server.serve()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)