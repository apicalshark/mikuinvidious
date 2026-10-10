# Caching

Cache lives in Redis. Minutes; `0` disables the route. Cached pages return
`X-Cache: HIT` / `MISS`. No header = bypassed or disabled.

| Key | Env var | Default | Route |
| :--- | :--- | :--- | :--- |
| `space_minutes` | `SPACE_CACHE_MINUTES` | 5 | `/space/<mid>` page 1 |
| `space_json_minutes` | `SPACE_JSON_CACHE_MINUTES` | 5 | `/space/<mid>/json` |
| `video_minutes` | `VIDEO_CACHE_MINUTES` | 15 | `/video/<vid>` (info/tags/related/parts; comments always live) |
| `bangumi_minutes` | `BANGUMI_CACHE_MINUTES` | 60 | `/bangumi/view/<ssid>` |
| `author_minutes` | `AUTHOR_CACHE_MINUTES` | 30 | `/author/<mid>` |
| `article_minutes` | `ARTICLE_CACHE_MINUTES` | 30 | `/read/<cid>`, `/opus/<cid>` |
| `audio_minutes` | `AUDIO_CACHE_MINUTES` | 30 | `/audio/<auid>`, `/audio_list/<amid>` |
| `home_minutes` | `HOME_CACHE_MINUTES` | 30 | homepage feed |

Two design points:

1. **Space page and JSON share one upstream payload** (`space:data:<mid>`). One
   Bilibili request serves both routes. The key expires on the longer TTL, but
   each route enforces its own max-age on read.
2. **Only healthy payloads are cached.** Errors and empties (e.g. risk-control
   "0 videos") are served live, never stored. The cache can't pin a failure.

Deep pages (`?i=N`, N>1) get per-page keys (`space:data:<mid>:<pn>`).
`?listen=1` and `?format=` exports bypass the cache.
