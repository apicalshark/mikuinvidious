# Caching

Cache lives in Redis. All values are minutes; `0` disables that route's cache.
Every cached page returns an `X-Cache: HIT` / `MISS` header; a missing header means
the request bypassed the cache or that route's cache is off.

| Key | Env var | Default | Route |
| :--- | :--- | :--- | :--- |
| `space_minutes` | `SPACE_CACHE_MINUTES` | 5 | `/space/<mid>` page 1 |
| `space_json_minutes` | `SPACE_JSON_CACHE_MINUTES` | 5 | `/space/<mid>/json` |
| `video_minutes` | `VIDEO_CACHE_MINUTES` | 15 | `/video/<vid>` (info/tags/related/parts; comments always live) |
| `bangumi_minutes` | `BANGUMI_CACHE_MINUTES` | 60 | `/bangumi/view/<ssid>` |
| `author_minutes` | `AUTHOR_CACHE_MINUTES` | 30 | `/author/<mid>` |
| `article_minutes` | `ARTICLE_CACHE_MINUTES` | 30 | `/read/<cid>` and `/opus/<cid>` |
| `audio_minutes` | `AUDIO_CACHE_MINUTES` | 30 | `/audio/<auid>` and `/audio_list/<amid>` |
| `home_minutes` | `HOME_CACHE_MINUTES` | 30 | homepage feed |

## Core design

1. **Space page and JSON share one upstream payload** (`space:data:<mid>`): a single
   Bilibili request serves both routes. The key expires after the longer of the two
   TTLs, but each route still enforces its own max-age on read — so differing TTLs
   each take strict effect.
2. **Only healthy payloads are cached**: error responses and empty data (e.g. the
   "0 videos" returned under risk control) are never written; they're served live
   instead. Errors never enter the cache, so the cache can't pin a failure in place.

Deep pages (`?i=N`, N>1) each get their own per-page key
(`space:data:<mid>:<pn>`). `?listen=1` and `?format=` exports bypass the cache.
