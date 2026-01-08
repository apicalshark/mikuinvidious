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
import os
import sys
from datetime import datetime

import app as app_module  # noqa: F401
import uvloop
from shared import app, appconf, close_global_client


async def monitor_fd():
    while True:
        try:
            # Count open file descriptors via /proc/self/fd (Linux specific)
            fd_count = len(os.listdir("/proc/self/fd"))
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sys.stderr.write(f"[{timestamp}] Open FDs: {fd_count}\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"Error monitoring FDs: {e}\n")
            sys.stderr.flush()
        await asyncio.sleep(600)


async def main():
    # Start FD monitor in background
    asyncio.create_task(monitor_fd())

    # Register shutdown hook
    app.after_serving(close_global_client)

    host = appconf["server"]["host"]
    port = appconf["server"]["port"]

    sys.stderr.write(f"Starting MikuInvidious (ASGI) on {host}:{port}\n")
    sys.stderr.flush()


if __name__ == "__main__":
    try:
        if sys.version_info >= (3, 11):
            with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
                runner.run(main())
        else:
            uvloop.install()
            asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
