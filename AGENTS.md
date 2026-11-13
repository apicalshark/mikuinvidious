# MikuInvidious — Agent Guidelines

MikuInvidious is a lightweight, privacy-focused Bilibili frontend built with Python 3.15+, Quart (ASGI), Granian, Caddy (reverse proxy), Redis, and optional Cloudflare WARP (SOCKS5).

Detailed specs: [`doc/architecture.md`](doc/architecture.md) | [`doc/dash_architecture.md`](doc/dash_architecture.md) | [`doc/configuration.md`](doc/configuration.md) | [`doc/space_risk_control.md`](doc/space_risk_control.md) | [`doc/fingerprints.md`](doc/fingerprints.md) | [`doc/bilibili_api_reference.md`](doc/bilibili_api_reference.md)

---

## Critical Invariants & Gotchas (Do Not Regress)

- **DASH CDN User-Agent:** MUST send web Chrome UA (`get_cdn_headers()` in `python/miku/core/fingerprints.py`). Android UA (`BiliDroid`) triggers HTTP 403 on `.bilivideo.com`.
- **Ticket Cookie vs. Header:** NEVER send the `bili_ticket` cookie to web APIs (triggers anti-bot `v_voucher` precheck). Send it ONLY via the `x-bili-ticket` *header* for CDN/DASH proxying.
- **Redis Fast-Fail:** Hot paths must fail fast (`REDIS_SOCKET_TIMEOUT=1s`, `REDIS_RETRY=0`). In DASH track proxying, Redis failure must degrade to playurl fetch with a 30s cooldown (`_DASH_REDIS_COOLDOWN`) and use in-memory cache backup (`_DASH_MEM_TTL=30s`).
- **DNS SSRF Guard:** Uses stale-while-revalidate (`python/miku/core/dns_cache.py`) to keep DNS resolution off the hot fragment path.
- **Datacenter IP Risk-Control:** Datacenter IPs encounter HTTP 412 / `-352` without WARP. Space routes fall back to `/x/web-interface/card` and `/x/series/recArchivesByKeywords` with `dm_img_*` fingerprints. Throttled empty responses set `User._degraded` and emit `X-Degraded`.
- **Playurl Risk Gate:** Detects `code==0 + data.v_voucher`; retries once via avoidance lane (`isGaiaAvoided=true`, `dm_img_*` WebGL template), then falls back to PGC.
- **UGC Fake-404 on PGC:** Risk control causes UGC endpoints to return `-404` for PGC titles. Never let UGC `cid` resolution failure veto the PGC playurl path (`ep_id` fallback).
- **Playback vs. Download Design:**
  - *Playback:* Fails fast (2 mirrors, 4s handshake, 3s sweep budget). dash.js owns abandon/ABR. Reuses upstream sockets (`_PlaybackKeepAlivePool`).
  - *Downloads:* Persists (adaptive pacing ladder, 1s slowdown watchdog, 10s cooldown on slow probes, 32MB segment resumes).
  - *Never unify the two loops.*
- **CDN Token Refresh:** URLs have ~2h signatures. Refresh playurl on 403 sweeps or expired timestamps; NEVER refresh on slow edges/tarpits.
- **Live Stream Format:** Server-side policy is FLV first, HLS fallback (`LIVE_PREFER_HLS=true` reverses). VOD never loads live player scripts.

---

## Development, Testing & Verification Commands

### Verification Decision Matrix (MANDATORY)

Do not run tests indiscriminately. Evaluate the scope of your changes first:

| Modification Scope | Required Actions | Strictly FORBIDDEN / Redundant Actions |
| :--- | :--- | :--- |
| **Documentation / Markdown only** (`*.md`, `doc/*`) | Check git status & file links | **DO NOT run unit tests, compileall, or linters.** |
| **Frontend assets only** (`static/*`, `templates/*`) | `npm run lint:frontend`<br>`npm run build:css` (if Tailwind CSS modified)<br>`npm run build:i18n` (if translatable strings change: extracts the catalog, then updates and compiles translations)<br>`npm run check:i18n` (to verify catalogs) | **DO NOT run Python unit tests.** |
| **Python code modified** (`python/*`, `tests/*`) | `uv run ruff check python`<br>`uv run python -m compileall -q python tests tools` (syntax only; verify affected imports separately)<br>`npm run build:i18n` (if translatable strings change: extracts the catalog, then updates and compiles translations)<br>`npm run check:i18n` (if catalogs change)<br>`NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1" PYTHONPATH=python uv run python -m unittest discover tests` | Do not skip bytecode or ruff checks before PRs. |

### Command Reference

```bash
# Python Test Suite (Run ONLY when python/* or tests/* change)
NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1" PYTHONPATH=python uv run python -m unittest discover tests

# Bytecode Compilation Check (Syntax Errors; Does Not Verify Imports)
# Verify affected imports separately in the configured application environment.
uv run python -m compileall -f python tests tools

# Python Linting & Formatting
uv run ruff check python
uv run ruff format python

# Frontend Linting & Formatting (When static JS/CSS/templates change)
npm run lint:frontend
npm run format:frontend

# CSS & Static Asset Rebuild (When tailwind styles or vendored libs change)
npm run build:css     # after editing static/themes/modern/css/tailwind-input.css
npm run sync:static   # after bumping packages in package.json

# i18n Translation Check & Build
npm run check:i18n
npm run build:i18n
```

---

## Codebase Map

- `python/main.py`: Granian ASGI production launcher (`miku.app:app`, 1 worker).
- `python/miku/app.py`: Quart app initialization, error handlers, and blueprint registration.
- `python/miku/state.py`: Shared singleton runtime state (`appredis`, HTTPX client, template renderer).
- `python/miku/core/`: Config (`config.py`), app factory (`app.py`), CSRF (`csrf.py`), rate limiting (`rate_limit.py`), DNS cache (`dns_cache.py`), fingerprints (`fingerprints.py`), IP slots (`ip_slots.py`), and secrets encryption (`secrets_encryption.py`).
- `python/miku/services/`: Cache (`cache.py`), concurrency (`concurrency.py`), credentials (`credential.py`), network (`network.py`), Redis (`redis.py`), and ticket lifecycle (`ticket.py`).
- `python/miku/views/`: View blueprints (`home.py`, `video.py`, `space.py`, `bangumi.py`, `live.py`, `audio.py`) re-exported via `miku.views`.
- `python/miku/dash/`: DASH streaming & download engine re-exported via `miku.dash`:
  - `common.py`: Constants, types, mirror health & range-shy caches.
  - `fetch.py`: Playurl retrieval, Redis caching, signature freshness.
  - `mpd.py`: Dynamic ISO/IEC 23009-1 MPD manifest generation.
  - `stream.py`: HTTP Range track proxying, multi-mirror failover, watchdog.
  - `download_track.py`: Segmented downloading, adaptive pacing, ffmpeg muxing.
  - `download_job.py`: Background job registry, queue concurrency, REST API.
- `python/miku/media/`: Progressive media (`proxy.py`), live stream raw-socket proxying (`live_manager.py`, `stream.py`), danmaku (`danmaku.py`), image cache (`image_cache.py`), transformers (`transformers.py`), and resolvers (`b23_resolver.py`, `nyaa.py`, `extra.py`, `res.py`, `refresher.py`).
- `python/miku/api/`: Local async Bilibili API wrapper (`client.py`, `wbi.py`, `bvid.py`, `video.py`, `user.py`, `live.py`, `bangumi.py`, etc.).
- `python/miku/ui/`: Jinja2 filters (`filters.py`), i18n (`i18n.py`, `i18n_strings.py`), theme renderer (`renderer.py`), and HTML sanitizer (`sanitizer.py`).

---

## Conventions & Infrastructure

- **License:** GNU AGPL-3.0.
- **Theming:** Jinja2 templates in `templates/themes/`. Active theme is `modern` (Tailwind CSS, mobile-first, light/dark).
- **Docker (`compose.yml`):**
  - `app`: Granian running Quart (`:8080`).
  - `caddy`: Reverse proxy & static server (`:8000`).
  - `redis`: Session and cache persistence.
  - `warp`: Cloudflare WARP SOCKS5 proxy (`:1080`, optional, for datacenter IPs).
- **Config:** Managed via `config.toml` (sample: `config.toml.sample`) or environment variables. `site_url` defaults to `http://localhost:8888`.

---

## MCP Tool Usage

Use `call_mcp_tool(ServerName, ToolName, Arguments)`:
- **Web Fetching:** Prefer `webfetch` (`fetch`) or `exa` (`web_fetch_exa`) over shell curls.
- **Library Docs:** Use `context7_context7` (`query-docs`) for Quart, Tailwind, Redis, Caddy.
- **Web Standards:** Use `mdn` (`get-doc`, `get-compat`) for Web APIs/CSS.
- **Complex Tasks:** Use `sequential-thinking` (`sequentialthinking`) for multi-step reasoning.