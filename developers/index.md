# Developer Guide — Overview

Stack: Python 3.14+, Quart (ASGI), Granian (Rust ASGI server), Redis, Jinja2,
Tailwind CSS. The Bilibili API is our own `python/api/` wrapper (migrated from
the archived `bilibili-api-python` in September 2026).

## Code map

| File | Responsibility |
| :--- | :--- |
| `python/app.py` | Build the Quart app, register blueprints, error handling |
| `python/views.py` | Main routes: home, search, video, space, live, audio |
| `python/views_bangumi.py` | Bangumi blueprint and Nyaa search |
| `python/shared.py` | Config, `httpx` clients, Redis, theme helpers |
| `python/proxy.py` | Progressive media and image proxy (`CdnConnection` raw socket) |
| `python/dash_proxy.py` | Full DASH chain: playurl, MPD generation, track proxy, muxed downloads |
| `python/live_manager.py` | Live connection management, chunk buffering, heartbeat injection |
| `python/api/` | Bilibili API wrapper (WBI signing, tickets, retries) |
| `templates/themes/`, `static/` | Themes (`modern` is officially supported) and vendored players |

Suggested reading order: [Architecture](architecture) →
[Playback & download](playback-stack) → [Route reference](api-reference) →
[API wrapper](bilibili-api). For UI work see [Theming](theming) and
[Internationalization](i18n).
