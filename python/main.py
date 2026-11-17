# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import sys

# Import app to ensure it's initialized and hooks are registered
import miku.app as app_module  # noqa: F401
from granian import Granian
from granian.constants import Interfaces, Loops, TaskImpl
from granian.http import HTTP1Settings
from miku.state import appconf


def main():
    # Bind to configured host and port to allow cross-container communication
    host = appconf["server"]["host"]
    port = appconf["server"]["port"]

    # Support auto-reload in debug mode
    debug_mode = appconf["server"]["debug"]

    # Granian handles the event loop (uvloop) and ASGI interface natively.
    # We use the string target "miku.app:app" to allow potential multi-worker support.
    # NOTE: download jobs live in process memory (dash_proxy._download_jobs),
    # so workers must stay 1 — without sticky sessions, status/cancel/file
    # requests land on a worker that never saw the job (404/409). Revisit
    # only with shared job storage or guaranteed worker affinity.
    server = Granian(
        "miku.app:app",
        address=host,
        port=port,
        workers=1,
        interface=Interfaces.ASGI,
        loop=Loops.uvloop,
        task_impl=TaskImpl.asyncio,
        reload=debug_mode,
        http1_settings=HTTP1Settings(
            # Milliseconds (Granian default 30_000); keep-alive stays on by
            # default and has no idle timeout. Caddy reaches us over HTTP/1.1,
            # so HTTP2Settings (tuned or not) would be inert here.
            header_read_timeout=10_000,  # 10 seconds
        ),
        # Per-request access log (one line per segment/image/poll). Off by
        # default via [server] access_log / SERVER_ACCESS_LOG — it buries the
        # real [DashProxy]/[Init] app logs. Enable for traffic debugging.
        log_access=appconf["server"]["access_log"],
    )

    sys.stderr.write(f"Starting MikuInvidious (Granian) on {host}:{port}\n")
    sys.stderr.flush()

    server.serve()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
