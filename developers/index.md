# Developer Guide

MikuInvidious is an asynchronous, privacy-first web frontend and media proxy for Bilibili, built with Python 3.15+, Quart (ASGI), Granian, Redis, and Caddy.

---

## Technology Stack

- **Runtime & Framework**: Python 3.15+, [Quart](https://pgjones.gitlab.io/quart/) (async ASGI framework), [Granian](https://github.com/emmett-framework/granian)
- **Web Server & Reverse Proxy**: [Caddy](https://caddyserver.com/) (serves `/static/*` directly and forwards dynamic routes to Granian; default `:8000` listener uses HTTP, while TLS termination requires a configured domain or upstream TLS terminator)
- **Database / Cache**: [Redis](https://redis.io/) (required for playurl normalization, page caching, rate limiting, and ticket storage; Quart client sessions are signed client cookies)
- **Local API Wrapper**: Self-maintained `python/api/` module with WBI signing, `bili_ticket` generation, Gaia risk evasion, and PGC fallback
- **Frontend & Players**: Tailwind CSS 4, `dash.js` (VOD DASH v5), `hls.js` + `mpegts.js` (Live streaming), `media-chrome`
- **Optional Egress**: Cloudflare WARP (SOCKS5 proxy on `:1080`) for bypassing datacenter IP risk control

---

## Codebase Map

| Subsystem | Key Files | Responsibility |
| :--- | :--- | :--- |
| **Core & App** | `python/main.py`, `python/miku/app.py`, `python/miku/state.py`, `python/miku/core/`, `python/miku/services/` | Quart application setup, global error handling, Redis connection pools, HTTP clients, and config loading. |
| **View Layer** | `python/miku/views/` | Route blueprints: `home.py`, `video.py`, `space.py`, `bangumi.py`, `live.py`, `audio.py` (re-exported via `miku.views`). |
| **DASH Engine** | `python/miku/dash/` | Modular pipeline: `mpd.py`, `fetch.py`, `stream.py`, `common.py`, `download_track.py`, `download_job.py` (re-exported via `miku.dash`). |
| **Media & Caches** | `python/miku/media/` | Image proxying with LRU disk cache (`proxy.py`, `image_cache.py`), live streams (`live_manager.py`, `stream.py`), danmaku, and resolvers. |
| **Networking & Defense** | `python/miku/core/` (`dns_cache.py`, `rate_limit.py`, `ip_slots.py`), `python/miku/media/stream.py` | Async DNS SSRF guard, Redis sliding-window rate limiting, IP slot concurrency balancing, raw socket TLS contexts. |
| **API Wrapper** | `python/miku/api/` (`client.py`, `video.py`, `user.py`, `bangumi.py`, etc.) | Upstream Bilibili HTTP client with WBI query signing, ticket generation, and datacenter IP risk mitigation. |
| **Templates & Static** | `templates/themes/modern/`, `static/` | Jinja2 templates, Tailwind styles, and vendored player scripts. |

---

## Suggested Reading Order

1. [Architecture](architecture) — High-level architecture, majestic request flowchart, and module inventory.
2. [Playback & download](playback-stack) — Modular DASH pipeline, socket keep-alive pool, mirror failover, and background downloads.
3. [Route & API reference](api-reference) — Public web endpoints, media proxy URLs, and JSON feeds.
4. [Bilibili API wrapper](bilibili-api) — WBI query signing, `bili_ticket` crypto, and risk control evasion.
5. [Theming](theming) & [Internationalization](i18n) — UI templates, Tailwind CSS 4 build pipeline, and gettext translation catalogs.
6. [Testing](testing) — Verification decision matrix, regression tests, and bytecode compilation.
