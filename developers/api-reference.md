# Route & API Reference

This document maps all public web routes, media streaming proxies, and auxiliary REST APIs provided by the application.

---

## 1. Application Web Routes

| Route | Blueprints | Description |
| :--- | :--- | :--- |
| `/` | `miku/views/home.py` | Home page with trending recommendation feeds. |
| `/search` | `miku/views/home.py` | Universal search with tabs (video, bangumi, users, live). |
| `/convert?url=<url>` | `miku/views/home.py` | Server-side `b23.tv` shortlink unwrapper and cleaner. |
| `/video/<vid>` | `miku/views/video.py` | Video page, metadata, player, parts, and comments. |
| `/video/<vid>:<idx>` | `miku/views/video.py` | Multi-part video page (colon notation or `?p=<idx>`). |
| `/video_listen/<vid>` | `miku/views/video.py` | Audio-only Listen Mode (`?listen=1`). |
| `/video/dash/<vid>/<idx>/manifest.mpd` | `miku/dash/mpd.py` | Dynamic ISO/IEC 23009-1 DASH MPD manifest (`?fresh=1` forces refresh; HMAC-signed). |
| `/bangumi` | `miku/views/bangumi.py` | Anime & Bangumi catalog index. |
| `/bangumi/view/<ssid>` | `miku/views/bangumi.py` | Season details, episodes grid, and PV sections. |
| `/bangumi/play/<epid>` | `miku/views/bangumi.py` | Bangumi episode player. |
| `/bangumi/api/nyaa/<ssid>` | `miku/views/bangumi.py` | Nyaa.si torrent integration API. |
| `/space/<mid>` | `miku/views/space.py` | Creator profile, video archive list (`?i=N` pagination). |
| `/space/<mid>/json` | `miku/views/space.py` | Unauthenticated machine-readable JSON feed of latest uploads. |
| `/author/<mid>` | `miku/views/space.py` | Compact author overview. |
| `/live`, `/live/<room_id>` | `miku/views/live.py` | Live stream catalog and room player. |
| `/live/chat/<room_id>` | `miku/views/live.py` | Real-time live danmaku chat SSE stream. |
| `/read/<cid>`, `/opus/<cid>` | `miku/views/space.py` | Proxied column articles and dynamic opus posts. |
| `/audio/<auid>` | `miku/views/audio.py` | Single audio track player with lyrics. |
| `/audio_list/<amid>` | `miku/views/audio.py` | Audio album/playlist player. |
| `/history` | `miku/views/home.py` | Browsing history view (stored in Redis, keyed by visitor cookie). |
| `/preferences` | `miku/views/home.py` | User preferences (theme, language, player defaults). |
| `/licenses` | `miku/views/home.py` | Open-source software licenses and notices. |
| `/robots.txt` | `miku/views/home.py` | Dynamic robots crawler policy (`strict`, `relaxed`, `PLEASE_INDEX_EVERYTHING`). |

---

## 2. Media & Proxy Routes

| Route | Handler | Description |
| :--- | :--- | :--- |
| `/proxy/pic/s/<sig>/*` | `miku/media/proxy.py`, `image_cache.py` | HMAC-signed proxy for Bilibili images (`*.hdslb.com`) with local LRU disk caching, singleflight coalescing, and WebP conversion. |
| `/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>` | `miku/dash/stream.py` | HMAC-signed bounded HTTP Range DASH track proxy (max 32 MiB per request; open-ended rejected with 416). |
| `/proxy/live_hls` | `miku/media/proxy.py` | HMAC-signed server-side live HLS playlist and segment proxy (rewrites child URIs). |
| `/proxy/video/*` | `miku/media/proxy.py` | Progressive MP4 proxy for legacy `durl` uploads lacking DASH. |
| `/proxy/download/<vid>/<idx>/<qual>` | `miku/media/proxy.py` | Legacy direct media download endpoint for non-JavaScript environments. |
| `/proxy/live/disconnect` (POST) | `miku/views/live.py` | Gracefully closes active upstream live stream connections. |
| `/res/danmaku/<vid>[:<idx>]` | `miku/media/res.py`, `danmaku.py` | Fetches, decodes protobuf, and emits Danmaku XML (HMAC-signed). |
| `/res/subtitle/<vid>[:<idx>[:<lan>]]` | `miku/media/res.py` | Fetches and formats video subtitle tracks (HMAC-signed). |

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
