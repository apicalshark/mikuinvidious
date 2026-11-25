# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
import sys

from miku.core.logging import configure_logging, get_granian_log_dictconfig

configure_logging()

from miku.state import appconf  # noqa: E402

if appconf["server"]["debug"]:
    configure_logging(debug=True)

# Import app to ensure it's initialized and hooks are registered
import miku.app as app_module  # noqa: E402, F401
from granian import Granian  # noqa: E402
from granian.constants import Interfaces, Loops, TaskImpl  # noqa: E402
from granian.http import HTTP1Settings  # noqa: E402
from granian.log import LogLevels  # noqa: E402


def main():
    # Bind to configured host and port to allow cross-container communication
    host = appconf["server"]["host"]
    port = appconf["server"]["port"]

    # Support auto-reload in debug mode
    debug_mode = appconf["server"]["debug"]
    log_level = LogLevels.debug if debug_mode else LogLevels.info

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
        log_level=log_level,
        log_dictconfig=get_granian_log_dictconfig(level=log_level.value.upper()),
        # Per-request access log (one line per segment/image/poll). Off by
        # default via [server] access_log / SERVER_ACCESS_LOG — it buries the
        # real [DashProxy]/[Init] app logs. Enable for traffic debugging.
        log_access=appconf["server"]["access_log"],
        log_access_format='%(addr)s - "%(method)s %(path)s %(protocol)s" %(status)d %(dt_ms).3f',
    )

    logging.getLogger("miku.app").info(f"[Init] Starting MikuInvidious (Granian) on {host}:{port}")

    server.serve()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
