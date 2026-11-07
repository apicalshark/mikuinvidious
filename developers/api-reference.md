# Route & API Reference

This document maps all public web routes, media streaming proxies, and auxiliary REST APIs provided by the application.

---

## 1. Application Web Routes

| Route | Blueprints | Description |
| :--- | :--- | :--- |
| `/` | `views_home.py` | Home page with trending recommendation feeds. |
| `/search` | `views_home.py` | Universal search with tabs (video, bangumi, users, live). |
| `/convert?url=<url>` | `views_home.py` | Server-side `b23.tv` shortlink unwrapper and cleaner. |
| `/video/<vid>` | `views_video.py` | Video page, metadata, player, parts, and comments. |
| `/video/<vid>:<idx>` | `views_video.py` | Multi-part video page (colon notation or `?p=<idx>`). |
| `/video_listen/<vid>` | `views_video.py` | Audio-only Listen Mode (`?listen=1`). |
| `/video/dash/<vid>/<idx>/manifest.mpd` | `dash_mpd.py` | Dynamic ISO/IEC 23009-1 DASH MPD manifest (`?fresh=1` forces refresh). |
| `/bangumi` | `views_bangumi.py` | Anime & Bangumi catalog index. |
| `/bangumi/view/<ssid>` | `views_bangumi.py` | Season details, episodes grid, and PV sections. |
| `/bangumi/play/<epid>` | `views_bangumi.py` | Bangumi episode player. |
| `/bangumi/api/nyaa/<ssid>` | `views_bangumi.py` | Nyaa.si torrent integration API. |
| `/space/<mid>` | `views_space.py` | Creator profile, video archive list (`?i=N` pagination). |
| `/space/<mid>/json` | `views_space.py` | Unauthenticated machine-readable JSON feed of latest uploads. |
| `/author/<mid>` | `views_space.py` | Compact author overview. |
| `/live`, `/live/<room_id>` | `views_live.py` | Live stream catalog and room player. |
| `/live/chat/<room_id>` | `views_live.py` | Real-time live danmaku chat SSE stream. |
| `/read/<cid>`, `/opus/<cid>` | `views_space.py` | Proxied column articles and dynamic opus posts. |
| `/audio/<auid>` | `views_audio.py` | Single audio track player with lyrics. |
| `/audio_list/<amid>` | `views_audio.py` | Audio album/playlist player. |
| `/history` | `views_home.py` | Browsing history view (stored in Redis, keyed by visitor cookie). |
| `/preferences` | `views_home.py` | User preferences (theme, language, player defaults). |
| `/licenses` | `views_home.py` | Open-source software licenses and notices. |
| `/robots.txt` | `views_home.py` | Dynamic robots crawler policy (`strict`, `relaxed`, `PLEASE_INDEX_EVERYTHING`). |

---

## 2. Media & Proxy Routes

| Route | Handler | Description |
| :--- | :--- | :--- |
| `/proxy/pic/*` | `proxy.py`, `image_cache.py` | Proxies Bilibili images (`*.hdslb.com`) with local LRU disk caching and WebP conversion. |
| `/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>` | `dash_stream.py` | Bounded HTTP Range DASH track proxy (max 32 MiB per request; open-ended rejected with 416). |
| `/proxy/video/*` | `proxy.py` | Progressive MP4 proxy for legacy `durl` uploads lacking DASH. |
| `/proxy/download/<vid>/<idx>/<qual>` | `proxy.py` | Legacy direct media download endpoint for non-JavaScript environments. |
| `/proxy/live/disconnect` (POST) | `views_live.py` | Gracefully closes active upstream live stream connections. |
| `/res/danmaku/<vid>[:<idx>]` | `res.py`, `danmaku.py` | Fetches, decodes protobuf, and emits Danmaku XML. |
| `/res/subtitle/<vid>[:<idx>[:<lan>]]` | `res.py` | Fetches and formats video subtitle tracks. |

---

## 3. Background Download & Component REST APIs

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/download` | POST | Enqueues a multi-track background download job → returns `{"job_id": "<uuid>"}`. |
| `/download/status/<job>` | GET | Polls download progress, speed, and phase (`queued`, `resolving`, `downloading`, `muxing`, `ready`). |
| `/download/file/<job>` | GET | Streams the completed, faststart-muxed `.mp4` file. |
| `/download/cancel/<job>` | POST | Aborts active background download and purges temporary disk files. |
| `/api/component/player/<vid>/<idx>` | GET | HTML snippet for player component. |
| `/api/component/meta/<vid>/<idx>` | GET | HTML snippet for video metadata. |
| `/api/component/comments/<vid>/<idx>/more` | GET | Paginated video comments. |
| `/api/component/comments/<vid>/<rpid>` | GET | Sub-thread replies for a specific comment. |
| `/toggle_theme` | POST | Updates theme cookie preference. |
| `/set_lang` | POST | Updates locale cookie preference. |
