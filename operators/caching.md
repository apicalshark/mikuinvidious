# Caching Architecture

MikuInvidious employs a multi-tiered caching architecture across memory, Redis, and local disk to achieve sub-millisecond response times while preventing upstream rate-limiting.

---

## 1. Page Cache (Redis)

Standard HTML views and metadata payloads are cached in Redis. TTLs are configured in minutes under `[cache]` in `config.toml` (or via environment variables). Setting any TTL to `0` disables caching for that specific route.

| Cache Key / Route | Default TTL | Environment Variable | Description |
| :--- | :--- | :--- | :--- |
| `/space/<mid>` | 5 min | `SPACE_CACHE_MINUTES` | Page 1 space payload. Shared with JSON feed. |
| `/space/<mid>/json` | 5 min | `SPACE_JSON_CACHE_MINUTES` | Normalized JSON feed payload. Shared with HTML space. |
| `/video/<vid>` | 15 min | `VIDEO_CACHE_MINUTES` | Video metadata (info, tags, related, parts). Comments stay live. |
| `/bangumi/view/<ssid>` | 60 min | `BANGUMI_CACHE_MINUTES` | Anime season metadata and episode list. |
| `/author/<mid>` | 30 min | `AUTHOR_CACHE_MINUTES` | Compact author profile payload. |
| `/read/<cid>`, `/opus/<cid>` | 30 min | `ARTICLE_CACHE_MINUTES` | Parsed article and dynamic post content. |
| `/audio/<auid>` | 30 min | `AUDIO_CACHE_MINUTES` | Audio track metadata and stream signatures. |
| `/` (Homepage) | 30 min | `HOME_CACHE_MINUTES` | Homepage trending recommendation feed. |

All cached page responses emit an `X-Cache: HIT` or `X-Cache: MISS` HTTP response header.

### Key Architectural Principles
1. **Shared Space Payload**: The HTML space view (`/space/<mid>`) and JSON feed (`/space/<mid>/json`) share a single upstream Bilibili request (`space:data:<mid>`). The key expires on the longer TTL, with each route enforcing its own max-age on read.
2. **Never Pin Failures**: Only healthy responses are cached. Upstream errors, network timeouts, or risk-control empty responses (e.g., empty video archives) are served live and never written to cache.
3. **Bypass Rules**: Requests with `?listen=1` or custom format exports bypass the HTML page cache.

---

## 2. DASH Playurl Cache (Dual-Layer)

Video playback uses a high-performance dual-layer cache in `python/dash_fetch.py`:
- **L1 In-Memory LRU Cache**: Retains up to 64 parsed stream manifests in process memory (`_DASH_MEM_MAX = 64`, `_DASH_MEM_TTL = 30s`). Consecutive video fragment requests bypass Redis lookups entirely.
- **L2 Redis Cache**: Normalizes stream mirror URLs and stores playurl responses under `miku_dash_<vid>_<idx>` with an 1800-second TTL.
- **Fail-Fast Degradation**: If Redis times out or is temporarily disconnected, the DASH engine degrades gracefully to live playurl fetches and enters a 30-second Redis cooldown (`_DASH_REDIS_COOLDOWN`) to keep media streaming responsive.

---

## 3. Image Disk Cache (Local LRU)

Image proxy requests (`/proxy/pic/*`) in `python/image_cache.py` are saved to a dedicated local disk directory:
- **Default Directory**: `data/img_cache` (configured via `IMAGE_CACHE_DIR`, mapped to Docker volume `img_cache`).
- **Quota & Trimming**: Capped at 2048 MB (`IMAGE_CACHE_MAX_SIZE_MB`). When disk usage exceeds this threshold, an automatic LRU prune trims total disk consumption down to 80% of the limit.
- **Retention**: Images are retained for 7 days (`IMAGE_CACHE_TTL_DAYS`).
- **Edge Performance**: Behind Caddy or Cloudflare, cached image hits return in under 1 millisecond.
