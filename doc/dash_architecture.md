# DASH Playback & Download Subsystem Specification

MikuInvidious employs Dynamic Adaptive Streaming over HTTP (ISO/IEC 23009-1 DASH) as its primary VOD pipeline, with progressive MP4 (`durl`) retained as an automated fallback for legacy uploads.

---

## 1. Architectural Overview

The DASH engine is divided into six modular components located in `python/`:

```
               [ Client Browser (dash.js v5) ]
                              |
            +-----------------+-----------------+
            |                                   |
            v /video/dash/.../manifest.mpd      v /proxy/dash/... (Fragments)
    +-----------------+                 +-----------------------+
    |   dash_mpd.py   |                 |    dash_stream.py     |
    | Dynamic MPD Gen |                 | HTTP Range Track Proxy|
    +-----------------+                 +-----------------------+
            |                                       |
            v                                       v
    +-----------------+                 +-----------------------+
    |  dash_fetch.py  |                 |    dash_common.py     |
    | Playurl Fetch & |                 | Mirror Health & Cache |
    | Dual-Layer Cache|                 | Socket Keep-Alive Pool|
    +-----------------+                 +-----------------------+
            |                                       |
            +-------------------+-------------------+
                                |
                                v
               [ Background Download Queue ]
                                |
                +-------------------------------+
                |     dash_download_job.py      |
                |   Concurrency & REST Endpoints|
                +-------------------------------+
                                |
                +-------------------------------+
                |     dash_download_track.py    |
                | Segment Downloader & Muxer    |
                +-------------------------------+
```

---

## 2. Core Subsystems

### A. Playurl Fetch & Dual-Layer Caching (`dash_fetch.py`)
- **Resolution Strategy:** Requests upstream UGC playurl via WBI with `fnval=4048, fourk=1, qn=120`, falling back to `/pgc/player/web/v2/playurl` for Bangumi/PGC media.
- **Dual-Layer Cache:**
  - **L1 Process Memory:** In-memory cache (`_DASH_MEM_TTL = 30s`) serves hot fragment bursts. Bypasses Redis network latency so consecutive fragment requests never pay a Redis lookup overhead.
  - **L2 Redis Cache:** Cached under `miku_dash_<vid>_<idx>` with an 1800-second TTL. Keys normalize `base_url` and `backup_url` lists.
- **Fail-Fast Protection:** Hot paths degrade gracefully on Redis timeouts or disconnections without throwing exceptions. A 30-second cooldown prevents repeated Redis timeouts on subsequent fragments. Expired or 403-invalidated tokens trigger proactive memory drops.

### B. Dynamic MPD Generation (`dash_mpd.py`)
- Emits a standards-compliant `isoff-on-demand` MPD at `/video/dash/<vid>/<idx>/manifest.mpd`.
- Only audio and video tracks containing valid `SegmentBase.indexRange` headers are included.
- BaseURLs dynamically point to the local track proxy (`/proxy/dash/...`), resolved per request rather than hardcoded in the manifest.

### C. Track Proxying & Upstream Socket Pooling (`dash_stream.py`)
- **Range Forwarding:** Proxies client `Range` headers using raw-socket `CdnConnection` (direct or via WARP SOCKS5). Responds with `206 Partial Content`.
- **Derivable Content-Length:** dash.js relies on computable sizes for ABR and throughput profiling. Responses missing explicit content sizes are rejected or synthesized from `Content-Range` to avoid client abandon timeouts.
- **Upstream Socket Reuse (`_PlaybackKeepAlivePool`):** Banks exactly consumed, length-framed connections by `(proxy, url)`. Consecutive fragments reuse existing TCP+TLS handshakes, significantly reducing latency and eliminating slow-start penalties. Idle connections are reaped after 30 seconds (pool capacity: 8).
- **Persistent TLS Context:** Reuses a process-global `_TLS_CONTEXT` in `stream.py` to prevent redundant CA certificate bundle parsing on every connection.

### D. Mirror Failover & Edge Health Memory (`dash_common.py`)
- **Mirror Selection:** Candidates are sorted healthy-first. Transport failures trigger a 120-second sick cooldown, while successful requests immediately clear error marks.
- **Range-Shy Edge Detection:** Thin CDN edges that return mid-file `416 Range Not Satisfiable` errors are marked as "range-shy" with offset granularity, preventing future fragments from attempting doomed mirrors.
- **Wall-Clock Sweep Budget:** Sweeps are bounded by time rather than mirror count (`DASH_PLAYBACK_MIN_MIRRORS = 2`, `DASH_PLAYBACK_MIRROR_BUDGET = 3s`, `DASH_PLAYBACK_ATTEMPT_TIMEOUT = 4s`). Fast connection resets quickly check alternative mirrors, while slow edges fail over before client-side player timeouts trip.
- **Mid-Body Failover:** Stream dropouts or idle pauses past `DASH_BODY_IDLE_TIMEOUT` (5s) automatically resume the remaining byte range on the next available healthy mirror.

### E. Background Download Pipeline (`dash_download_*.py`)
- **Queue & Concurrency:** Managed by `dash_download_job.py` (capped at 3 concurrent downloads). Each job is assigned an unguessable UUID.
- **Segment Downloading:** Downloads video and audio tracks concurrently as 32MB `Range` segments with 1MB disk write batches.
- **Adaptive Pacing & Slowdown Watchdog:** Consecutive slow transfers trigger an adaptive backoff ladder (2s to 10s). A 1-second rolling watchdog detects edge throttling, dropping stalled connections and pausing before reconnecting.
- **Muxing & Delivery:** Completed tracks are remuxed with stream copy (`ffmpeg -c copy -movflags +faststart`) up to 1080p anonymous ceiling. Cancelled jobs cleanly abort CDN transfers and prune temporary files.

### F. Progressive `durl` Fallback (`proxy.py`)
- **Legacy Fallback:** UGC uploads lacking DASH tracks automatically fall back to progressive MP4 streams served by `/proxy/video/...`.
- **Validation:** `has_valid_dash_tracks()` inspects playurl payloads; if invalid, `fetch_durl_supported_src()` resolves quality-specific progressive sources.
