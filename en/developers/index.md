# Developer Guide

Stack: Python 3.14+, Quart (ASGI), Granian (Rust ASGI server), Redis, Jinja2,
Tailwind CSS. Own `python/api/` Bilibili wrapper (migrated from archived
`bilibili-api-python`, September 2026).

| File | Responsibility |
| :--- | :--- |
| `python/app.py` | Quart app, blueprints, error handling |
| `python/views.py` | Routes: home, search, video, space, live, audio |
| `python/views_bangumi.py` | Bangumi blueprint, Nyaa search |
| `python/shared.py` | Config, `httpx` clients, Redis, theme helpers |
| `python/proxy.py` | Progressive media + image proxy (`CdnConnection` raw socket) |
| `python/dash_proxy.py` | DASH chain: playurl, MPD, track proxy, muxed downloads |
| `python/live_manager.py` | Live connections, chunk buffering, heartbeat injection |
| `python/api/` | Bilibili wrapper (WBI, tickets, retries) |
| `templates/themes/`, `static/` | Themes (`modern` supported), vendored players |

Reading order: [Architecture](architecture) → [Playback](playback-stack) →
[Routes](api-reference) → [API wrapper](bilibili-api). UI work:
[Theming](theming), [i18n](i18n).
