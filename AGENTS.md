# MikuInvidious Project Overview

MikuInvidious is a free and open-source frontend for Bilibili, inspired by Invidious. It aims to provide a lightweight, privacy-focused experience for browsing Bilibili content without the need for heavy official clients or extensive tracking.

## Core Technologies

- **Language:** Python 3.14+
- **Web Framework:** [Quart](https://pgjones.gitlab.io/quart/) (Modern asynchronous web framework)
- **Web Server:** [Caddy](https://caddyserver.com/) (Reverse proxy) + [Granian](https://github.com/emmett-framework/granian) (Rust-powered ASGI server)
- **Database/Cache:** [Redis](https://redis.io/) (required for caching video URLs, session management, and credential storage)
- **API Wrapper:** Local `python/api/` module (replacing archived [bilibili-api-python](https://github.com/nemo2011/bilibili-api), see migration plan below)
- **Video Player:** `hls.js`, `mpegts.js`, and `dash.js` (for live streams, FLV, and DASH support)
- **Templating:** Jinja2 (with theme support)

## System Architecture

### High-Level Design

The system uses **Caddy** as a reverse proxy and static file server, which forwards application requests to **Granian** running the **Quart** (ASGI) application. All logic and proxying are handled within the Quart application using asynchronous I/O.

- **Reverse Proxy (Caddy):** Handles incoming traffic (port 8000), serves static assets, and proxies requests to the ASGI server.
- **ASGI Server (Granian):** Runs the Quart application (port 8080 in Docker).
- **App Logic (`app.py`):** Main entry point for the application, registering blueprints and routes.
- **Reverse Proxy (`proxy.py`):** Handles video and image streaming using Quart's async generators and `httpx`.
- **Network Transport:** Integrates with **Cloudflare WARP** (via SOCKS5) to route traffic to Bilibili.

### Request Flowchart

```mermaid
graph TD
    User((User / Browser))
    
    subgraph "Docker Host"
        Caddy["Caddy Reverse Proxy<br>(Port 8000)"]
        Granian["Granian ASGI Server<br>(Port 8080)"]
        
        subgraph "MikuInvidious App"
            Router{URL Path?}
            ProxyRes["Quart Proxy Blueprint<br>(proxy.py / live_manager.py)"]
            BangumiBP["Bangumi Blueprint<br>(views_bangumi.py)"]
            Views["Quart Views<br>(views.py / app.py)"]
            BiliAPI["Bilibili API Wrapper"]
            NyaaHelper["Nyaa.si Helper<br>(nyaa.py)"]
        end
        
        Redis[("Redis Cache")]
    end
    
    subgraph "Network Services"
        Warp["Cloudflare WARP<br>(SOCKS5 Proxy)"]
    end
    
    subgraph "External"
        BiliCDN["Bilibili CDN<br>(Videos/Images)"]
        BiliServers["Bilibili API Servers"]
        NyaaSI["Nyaa.si<br>(Torrents)"]
    end

    %% Flows
    User --> Caddy
    Caddy -- "Static Assets" --> Static[Static Files]
    Caddy -- "App Traffic" --> Granian
    Granian --> Router
    
    %% Proxy Path
    Router -- "/proxy/..." --> ProxyRes
    ProxyRes -- "Check Cache" --> Redis
    ProxyRes -- "Stream Content" --> Warp
    
    %% Bangumi Path
    Router -- "/bangumi/..." --> BangumiBP
    BangumiBP --> BiliAPI
    BangumiBP --> NyaaHelper
    
    %% App Path
    Router -- "Other Routes" --> Views
    Views -- "Get Metadata" --> BiliAPI
    
    %% API / External
    BiliAPI -- "Fetch Data" --> Warp
    NyaaHelper -- "Search" --> NyaaSI
    
    %% External Connections
    Warp --> BiliCDN
    Warp --> BiliServers
    
    %% Returns
    BiliCDN -.-> Warp -.-> ProxyRes -.-> Granian -.-> Caddy -.-> User
    BiliServers -.-> Warp -.-> BiliAPI -.-> Views/BangumiBP -.-> Granian -.-> Caddy -.-> User
    NyaaSI -.-> NyaaHelper -.-> BangumiBP -.-> Granian -.-> Caddy -.-> User
```

### Component Breakdown

- **Application Logic:**
  - `app.py`: Initializes the Quart app, error handlers, and registers blueprints.
  - `views.py`: Main routing logic for home, search, video, space, and author views.
  - `views_bangumi.py`: Blueprint for Bangumi (Anime/Show) indexing and playback.
  - `shared.py`: Centralized configuration, `httpx` client management, Redis connection, and theming utilities.
  - `proxy.py`: Quart Blueprint for media proxying. Uses a robust `ProxyResponse` class and `ClosingIterator` to prevent file descriptor leaks.
  - `live_manager.py`: Manages persistent live stream connections, chunk buffering, and heartbeat (Type 18) injection.
  - `danmaku.py`: Fetches and converts Bilibili danmaku.
  - `nyaa.py`: Scraper and helper for searching Nyaa.si torrents (integrated into Bangumi view).
  - `extra.py`: Utilities for article-to-HTML conversion and ID manipulation.
  - `transformers.py`: Data transformation logic to standardize API responses for the frontend.
  - `filters.py`: Custom Jinja2 template filters (e.g., date formatting).
  - `res.py`: Serves dynamic resources like Danmaku XML.
  - `refresher.py`: Utility to refresh Bilibili credentials.
  - `api/`: Local Bilibili API client (replacing bilibili-api-python).
    - `api/__init__.py`: Exports all public symbols (Credential, video, user, etc.)
    - `api/client.py`: HTTP client with Wbi signing, bili_ticket, auto-cookies, retry logic
    - `api/credential.py`: Credential class for auth
    - `api/exceptions.py`: ArgsException, ResponseCodeException
    - `api/video.py`: Video class (info, tags, related, pages, cid, playurl, danmaku)
    - `api/user.py`: User class (info, videos, articles)
    - `api/search.py`: search_by_type with enums
    - `api/comment.py`: get_comments with enums
    - `api/live.py`: LiveRoom, LiveDanmaku, live_area
    - `api/bangumi.py`: Bangumi class (meta, episodes, index)
    - `api/audio.py`: Audio, AudioList classes
    - `api/article.py`, `api/opus.py`: Article/Opus classes
    - `api/homepage.py`, `api/video_zone.py`: Homepage/zone feeds
    - `data/bangumi_index_params.json`: Copied from bilibili_api package

## Deep Analysis & Architectural Insights

### 1. Structural Integrity & Core Patterns

* **Quart Framework:** Chosen for its async capabilities, essential for high-concurrency streaming.
- **Unified Proxying:** Media streams (video/images) are handled via Quart blueprints, allowing for consistent application-level control, session management, and bypassing region blocks.
- **Dynamic Theming:** Templates are dynamically selected based on cookies or URL parameters. Currently focuses on the `modern` theme.

### 2. UI/UX Focus

* **Modern Theme:** Built with Tailwind CSS, supporting both Light and Dark modes. Features a responsive, mobile-first design inspired by Material Design 3.
- **Playback Experience:** Uses `hls.js`, `mpegts.js`, and `dash.js` for low-latency live streaming and high-quality DASH playback. Includes custom "Click to Play" recovery for autoplay-blocked browsers.

### 3. Codebase Health & Observations

* **Streaming Reliability:** Employs `ProxyResponse` (OO design) and `ClosingIterator` to ensure upstream `httpx` connections are closed properly, even on client disconnect.
- **Performance:** Image proxying uses an aggressive concurrency limit (50x) and CDN resizing (WebP) to ensure fast thumbnail loading.
- **Timeouts:** Uses long timeouts (up to 3 hours) for streaming routes to prevent idle drops during long-form content.

## Infrastructure (Docker)

The production infrastructure consists of four orchestrated services defined in `compose.yml`:

| Service | Image | Description |
| :--- | :--- | :--- |
| **`app`** | *(Local Build)* | Granian running the Quart application. Exposes port `8080` internally. |
| **`caddy`** | `caddy:alpine` | Reverse proxy and static asset server. Exposes port `8000`. |
| **`redis`** | `redis:alpine` | Persists sessions and caches API responses. |
| **`warp`** | `caomingjun/warp` | SOCKS5 proxy (port `1080`) for routing traffic to Bilibili API/CDN. |

## Configuration

Configuration is managed via `config.toml` (recommended) or Environment Variables.

- **`[site]`**: Metadata, Robots policy, and source code link.
- **`[server]`**: Host and port settings (Default: 8888 for manual run).
- **`[credential]`**: Bilibili cookies (SESSDATA, etc.) for authenticated access.
- **`[proxy]`**: Global toggle for media proxying.
- **`[redis]`**: Redis connection details.
- **`[render]`**: Configuration for article rendering (Pandoc support).

## Development & Deployment

### Docker Deployment (Recommended)

1. **Run with Docker Compose:**

    ```bash
    docker-compose up -d --build
    ```

2. **Access:** `http://localhost:8000`

### Manual Development Setup

1. **Prerequisites:** Python 3.14+, Redis server running.
2. **Install Dependencies:**

    ```bash
    uv sync
    ```

3. **Run:**

    ```bash
    uv run python/main.py
    ```

    Access at `http://localhost:8888` (or configured port).

## Recent Updates


## bilibili-api-python Migration Plan

**Status: COMPLETED (Sep 2 2026)**
**Goal:** Replace the abandoned `bilibili-api-python` (Nemo2011/bilibili-api, archived Jul 6 2026) with a local `python/api/` module using direct `httpx` calls.

### Migration Result

- All `bilibili-api-python` imports replaced with the local `python/api/` module across `shared.py`, `views.py`, `views_bangumi.py`, `extra.py`, `app.py`, `res.py`, `refresher.py`, `dash_proxy.py`.
- `bilibili-api-python` removed from `pyproject.toml`, `requirements.txt`, and `uv.lock`; `brotli` added as a direct dependency (needed by `api/live.py`).
- Verified working live from this host: Wbi mixin-key fetch, wbi-signed homepage feed (30 items), wbi-signed search (42 results), bili_ticket generation, buvid auto-generation.
- Note: some non-wbi endpoints (`/x/web-interface/view`, danmaku, playurl) return HTTP 412 / -352 from this datacenter IP when no WARP proxy is active; these are IP-level anti-bot responses, not migration defects, and resolve when the app routes through the WARP SOCKS5 proxy (as configured in production).

### Context

The `bilibili-api-python` library was archived after Bilibili sent a legal cease-and-desist. The last release (v17.4.2, Jun 19 2026) is frozen and will break when Bilibili changes APIs. There is **no maintained drop-in replacement**. MikuInvidious uses 20+ import sites across 8 files.

### Target: Only what this project uses

The `python/api/` module only needs to implement these specific endpoints and utilities:

#### 1. Credential & Auth (`python/api/credential.py`)
- [ ] `Credential` class: constructor (sessdata, bili_jct, buvid3, buvid4, dedeuserid, ac_time_value)
- [ ] `Credential.get_cookies()` -> dict
- [ ] `Credential.has_sessdata()`, `has_bili_jct()` -> bool
- [ ] `Credential.raise_for_no_sessdata()`, `raise_for_no_bili_jct()` -> raise `ArgsException`
- [ ] `Credential.check_refresh()`, `Credential.refresh()`, `Credential.get_cookies()` (for refresher.py)
- [ ] `sync()` wrapper (for refresher.py synchronous CLI)

#### 2. HTTP Client & Signing (`python/api/client.py`)
- [ ] `BiliClient` class: async httpx client with proxy support
- [ ] Wbi signing: `_get_mixin_key()` (fetch from nav API), `_enc_wbi()` (md5 with mixin key)
- [ ] `get_bili_ticket()` / `refresh_bili_ticket()` (HMAC-SHA256 to GenWebTicket endpoint)
- [ ] Auto cookies: buvid3/buvid4 auto-generation, bili_ticket injection
- [ ] Response processing: check HTTP status, extract `data`/`result`, check `code` field
- [ ] Wbi retry on -403 (recalculate mixin key)

#### 3. Exceptions (`python/api/exceptions.py`)
- [ ] `ArgsException` (for invalid request params)
- [ ] `ResponseCodeException(code, msg, data)` (Bilibili API error responses)

#### 4. Video Module (`python/api/video.py`)
- [ ] `Video(bvid=, credential=)` class
- [ ] `get_info()` -> GET `/x/web-interface/view` (NO wbi)
- [ ] `get_tags(page_index)` -> GET `/x/web-interface/view/detail/tag` (NO wbi)
- [ ] `get_related()` -> GET `/x/web-interface/archive/related` (NO wbi)
- [ ] `get_pages()` -> GET `/x/player/pagelist` (NO wbi)
- [ ] `get_cid(page_index)` -> extract from pages list
- [ ] `get_aid()` -> extract from info cache or bv2av
- [ ] `get_download_url(page_index, ...)` -> GET `/x/player/playurl` (NO wbi)
- [ ] `get_danmaku_xml(page_index)` -> GET danmaku XML

#### 5. User Module (`python/api/user.py`)
- [ ] `User(mid=, credential=)` class
- [ ] `get_user_info()` -> GET `/x/space/wbi/acc/info` (wbi required)
- [ ] `get_videos(pn, ps)` -> GET `/x/space/wbi/arc/search` (wbi required)
- [ ] `get_articles(pn, ps)` -> GET `/x/space/wbi/article` (wbi required)

#### 6. Search Module (`python/api/search.py`)
- [ ] `search_by_type(keyword, page, search_type, order_type)` -> GET `/x/web-interface/wbi/search/type` (wbi required)
- [ ] Enums: `SearchObjectType` (VIDEO, ARTICLE, USER, LIVE), `OrderVideo`, `OrderArticle`, `OrderUser`

#### 7. Comment Module (`python/api/comment.py`)
- [ ] `get_comments(oid, type_, page, order)` -> GET comment list (needs checking if wbi)
- [ ] Enums: `CommentResourceType` (VIDEO=1, AUDIO=2), `OrderType` (TIME, LIKE)

#### 8. Live Module (`python/api/live.py`)
- [ ] `LiveRoom(room_id, credential=)` class
- [ ] `get_room_info()` -> GET `/xlive/web-room/v1/index/getInfoByRoom`
- [ ] `get_room_play_info_v2(...)` -> GET `/xlive/web-room/v2/index/getRoomPlayInfo`
- [ ] `get_room_play_url()` -> GET `/xlive/web-room/v1/playUrl/playUrl` (fallback)
- [ ] `LiveDanmaku(room_id, credential=)` -> WebSocket client for DANMU_MSG events
- [ ] `LiveProtocol`, `LiveFormat` enums
- [ ] `live_area.get_list_by_area(area_id, page)` -> GET `/xlive/web-interface/v1/second/getList`

#### 9. Bangumi Module (`python/api/bangumi.py`)
- [ ] `Bangumi(ssid=, credential=)` class
- [ ] `get_meta()` -> GET `/pgc/review/user`
- [ ] `get_episode_list()` -> GET `/pgc/web/season/section`
- [ ] Index API: GET `/pgc/season/index/result` (via `Api` class)
- [ ] PGC season: GET `/pgc/view/web/season` (via `Api` class)
- [ ] Bundle `data/bangumi_index_params.json` (copy from bilibili_api package)

#### 10. Audio Module (`python/api/audio.py`)
- [ ] `Audio(auid, credential=)` class
- [ ] `get_info()` -> GET audio song info
- [ ] `get_download_url()` -> GET audio download URL
- [ ] `AudioList(amid, credential=)` class
- [ ] `get_song_list()` -> GET audio list songs
- [ ] `get_info()` on AudioList -> GET list info

#### 11. Article & Opus Modules (`python/api/article.py`, `python/api/opus.py`)
- [ ] `Article(cvid).get_info()` -> GET `/x/article/viewinfo`
- [ ] `Opus(cvid, credential=).get_info()` -> GET `/x/polymer/web-dynamic/v1/opus/detail`

#### 12. Homepage & Zones (`python/api/homepage.py`, `python/api/video_zone.py`)
- [ ] `homepage.get_videos()` -> GET `/x/web-interface/wbi/index/top/feed/rcmd` (wbi)
- [ ] `video_zone.get_zone_new_videos(zid, pn)` -> zone new videos API

#### 13. Low-level `Api` class (`python/api/client.py`)
- [ ] `Api(url, method, verify, credential, wbi, ...)` dataclass-like
- [ ] `.update_params(**kwargs)` -> self
- [ ] `.result` property -> awaited request result
- [ ] `.request()` -> execute request with wbi signing, cookie injection, response processing

### Migration Order (by criticality)

1. **`client.py`** + **`credential.py`** + **`exceptions.py`** (foundation - everything depends on this)
2. **`video.py`** (fixes the immediate "B站返回了空的數據" error)
3. **`user.py`** + **`search.py`** (space/search pages)
4. **`comment.py`** (video comments)
5. **`live.py`** (live streaming)
6. **`bangumi.py`** (anime/show pages)
7. **`audio.py`** (music pages)
8. **`article.py`** + **`opus.py`** (reading pages)
9. **`homepage.py`** + **`video_zone.py`** (homepage/zone pages)

### Files to Modify After Migration

| File | Current `bilibili_api` imports | Action |
|---|---|---|
| `python/shared.py` | `Credential`, `request_settings`, `get_bili_ticket`, `refresh_bili_ticket` | Replace with `api.credential`, `api.client` |
| `python/views.py` | 11 modules + `Api` | Replace all `bilibili_api` imports with `api.*` |
| `python/views_bangumi.py` | `bangumi`, `Api` | Replace with `api.bangumi`, `api.client` |
| `python/extra.py` | `ArgsException`, `Api`, `video` | Replace with `api.exceptions`, `api.client`, `api.video` |
| `python/app.py` | `exceptions` (ArgsException, ResponseCodeException) | Replace with `api.exceptions` |
| `python/res.py` | `video` | Replace with `api.video` |
| `python/refresher.py` | `Credential`, `sync` | Replace with `api.credential` |
| `pyproject.toml` | `bilibili-api-python>=17.4.2` | Remove dependency |
| `requirements.txt` | `bilibili-api-python>=17.4.2` | Remove dependency |

### Key Bilibili API Endpoints Reference

```
# Video (NO wbi)
GET https://api.bilibili.com/x/web-interface/view
GET https://api.bilibili.com/x/web-interface/view/detail/tag
GET https://api.bilibili.com/x/web-interface/archive/related
GET https://api.bilibili.com/x/player/pagelist
GET https://api.bilibili.com/x/player/playurl

# Video (wbi required)
GET https://api.bilibili.com/x/web-interface/wbi/view/detail

# User (wbi required)
GET https://api.bilibili.com/x/space/wbi/acc/info
GET https://api.bilibili.com/x/space/wbi/arc/search
GET https://api.bilibili.com/x/space/wbi/article

# Search (wbi required)
GET https://api.bilibili.com/x/web-interface/wbi/search/type

# Comment
GET https://api.bilibili.com/x/v2/reply/main

# Live
GET https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom
GET https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo
GET https://api.live.bilibili.com/xlive/web-room/v1/playUrl/playUrl
GET https://api.live.bilibili.com/xlive/web-interface/v1/second/getList

# Bangumi
GET https://api.bilibili.com/pgc/review/user
GET https://api.bilibili.com/pgc/web/season/section
GET https://api.bilibili.com/pgc/season/index/result
GET https://api.bilibili.com/pgc/view/web/season

# Audio
GET https://www.bilibili.com/audio/music-service-c/web/song/info
GET https://www.bilibili.com/audio/music-service-c/web/url
GET https://www.bilibili.com/audio/music-service-c/web/menu/info
GET https://www.bilibili.com/audio/music-service-c/web/song/of-menu

# Article & Opus
GET https://api.bilibili.com/x/article/viewinfo
GET https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail

# Homepage (wbi required)
GET https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd

# Wbi & Auth
GET https://api.bilibili.com/x/web-interface/nav  (for wbi mixin key)
POST https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket  (for bili_ticket)
```

### Wbi Signing Algorithm

```
1. GET /x/web-interface/nav -> extract wbi_img.img_url + wbi_img.sub_url
2. Take filenames, concat, apply OE permutation table -> 32-char mixin_key
3. For each request: add wts=unix_ts, sort params, urlencode, append mixin_key, MD5 -> w_rid
4. Retry on -403: clear mixin_key cache, re-fetch from nav, re-sign
```

### bili_ticket Algorithm

```
1. HMAC-SHA256(key="XgwSnGZ1p", message=f"ts{int(time.time())}")
2. POST /bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket with hexsign + key_id=ec02
3. Extract data.ticket from response
4. Cache for 3 days
```

## Development Conventions

- **License:** GNU GPL-3.0.
- **Theming:** Templates in `templates/themes/`. Current active theme is `modern`.
- **Static Assets:** `static/` contains `hls.js`, `mpegts.js`, and `danmaku.js`.
- **Proxying Strategy:**
  - **Images:** Always proxied with WebP optimization.
  - **Videos:** Proxied if `use_proxy=true`. Uses `httpx` with `follow_redirects=True`.
- **B23.tv:** Short links are resolved server-side.
