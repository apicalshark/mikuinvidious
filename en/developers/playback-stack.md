# Playback & download

DASH is the playback path. Progressive `durl` is fallback only, for uploads with
no DASH tracks.

## DASH chain

1. **Playurl** (`api/video.py:get_dash_playurl`,
   `dash_proxy.py:video_get_dash_for_qn`). UGC: WBI playurl
   (`fnval=4048, fourk=1, qn=120`). PGC: `/pgc/player/web/v2/playurl`. Redis cache
   `miku_dash_<vid>_<idx>`, 1800 s.
2. **MPD** (`generate_vod_mpd`), at `/video/dash/<vid>/<idx>/manifest.mpd`. Only
   tracks with valid `SegmentBase.indexRange`. BaseURLs point at the track proxy,
   resolved per request, never baked in.
3. **Track proxy** (`/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>`). Forwards the
   client `Range` untouched. Returns `206` + `Content-Range` / `Content-Length` /
   `Accept-Ranges`. dash.js segments via sidx itself. **Only length-computable 206
   counts** — the rest is skipped for the next mirror, or dash.js stalls on
   "non-computable download size" (dash.js#4716). `fragmentRequestTimeout` 60 s,
   abandon rules on.
4. **Failover.** Healthy mirrors first, cross-request sick-mirror memory
   (transport failures only — never 403/412/514; 120 s cooldown, cleared on
   success). 2 mirrors per fragment max, 4 s handshake
   (`DASH_PLAYBACK_ATTEMPT_TIMEOUT`): dash.js gives up after ~7 s anyway, so deeper
   traversal only burns its budget. Fail fast; let it retry or drop quality. After
   a completed handshake, disconnects / 5 silent seconds resume the remaining
   `Range` on the next mirror (byte-continuous, invisible to dash.js). Pure
   slowness never switches mirrors (backups are for faults; speed is ABR's job).
5. **Re-fetch on signature expiry only.** CDN URLs are time-signed (~2 h TTL;
   `expires|wsTime|txTime|um_deadline|deadline` all recognized). Re-fetch + retry
   only on 403-class sweep or confirmed stamp expiry (same-stamp dedup).
   `?fresh=1` forces refresh for player self-rescue. **Slowness never re-fetches**
   — a fresh signature on the same bad edge changes nothing.

## Frontend

`static/vjs/dash.min.js` is vendored v5.2.1 (synced from `dashjs` via
`npm run sync:static`). **Player code uses the v5 representations API only.**
v4 `getBitrateInfoListFor` / `setQualityFor` are gone. `DashPlayerManager`:
`static/themes/modern/js/player.js`.

## durl-only fallback

Some uploads return `durl` with no `dash` node under every endpoint/param
combination (verified 2026-09-11). `has_valid_dash_tracks()` gates it: no DASH →
`fetch_durl_supported_src()` (non-WBI playurl + PGC retries, parallel), cached as
`mikuinv_<vid>_<idx>_<qn>` (`+_bak` for backups), played via native
`/proxy/video/...`. PGC durl returns one node per qn — exact matches only, or the
menu lists one file under four names. **Progressive CDN fetches need a web Chrome
UA** (`build_cdn_headers()`; BiliDroid UAs get 403s from upos/akamaized edges).

## Downloads

`POST /download` opens a background job (max 3, uuid as ID), returns `{"job_id"}`.
The floating window (`static/themes/modern/js/download.js`) polls
`GET /download/status/<job>` (progress, speed, phase:
queued/resolving/downloading/muxing/ready) and auto-saves
`GET /download/file/<job>`. Cancel: `POST /download/cancel/<job>` (beacon works).
Drops the CDN connection, kills ffmpeg, clears temp files.

Same mirror/watchdog machinery as playback, **bigger budget** (8 s handshake, full
sweep, 5 backoff retries per segment): downloads have no client intelligence, the
server holds the connection instead. 32 MB Range segments, video+audio in parallel
(dash.js-style dual adaptation sets), 1 MB batched writes. Start-mirror rotation
per segment (sick sinks, M-CDN last). Adaptive pacing (2/4/6/8/10 s stepped backoff
on sustained slowness — per-IP throttling accumulates and decays; clean fast
stretches reset to zero). Keep-alive reuse. Slowdown watchdog (three consecutive
1 s windows under 300 KB/s after proving ≥1 MB/s → drop, reconnect after 10 s;
instant reconnects land back in the penalty box). Playurl refresh on dead
signatures only (403 sweep or stamp expiry), unlimited, same-stamp dedup.

Finished DASH downloads mux with `ffmpeg -c copy -movflags +faststart` (ffmpeg
ships in the app image). durl-only content is already a single MP4 — stored
directly.

## Constraints

- DASH CDN wants a web UA. Android app UAs get 403s.
- `bili_ticket` cookie never goes to web APIs (only `x-bili-ticket` on the CDN
  proxy) — otherwise the `v_voucher` pre-check fires.
- **Playback fails fast, downloads hold on. By design.** Don't unify the paths.
