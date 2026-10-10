# Playback & download architecture

DASH is the official playback path. Progressive `durl` is kept only as a fallback
for uploads with no DASH tracks.

## The DASH chain

1. **Fetch playurl** (`api/video.py:get_dash_playurl`,
   `dash_proxy.py:video_get_dash_for_qn`): UGC hits WBI playurl
   (`fnval=4048, fourk=1, qn=120`); PGC falls back to
   `/pgc/player/web/v2/playurl`. Cached in Redis (`miku_dash_<vid>_<idx>`, 1800s).
2. **Generate MPD** (`generate_vod_mpd`): served at
   `/video/dash/<vid>/<idx>/manifest.mpd`, containing only tracks with a valid
   `SegmentBase.indexRange`. BaseURLs point at the track proxy and are resolved
   per request — never baked into the manifest.
3. **Track proxy** (`/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>`): forwards the
   client's `Range` header untouched and returns `206` with `Content-Range` /
   `Content-Length` / `Accept-Ranges`. dash.js segments via sidx on its own, and
   **only a length-computable 206 counts as valid** — anything else is skipped in
   favor of the next mirror, or dash.js stalls on "non-computable download size"
   (dash.js#4716). `fragmentRequestTimeout` is 60s with abandon rules on.
4. **Failover policy**: healthy mirrors first, plus cross-request sick-mirror memory
   (only transport failures are recorded, never 403/412/514; 120s cooldown, cleared
   on success). Max 2 mirrors per fragment, 4s handshake
   (`DASH_PLAYBACK_ATTEMPT_TIMEOUT`) — dash.js gives up after ~7s on its own, so
   deeper traversal only burns the client's timeout budget. Fail fast and let it
   retry or drop quality. After a completed handshake, a disconnect or 5 silent
   seconds resume the remaining `Range` on the next mirror (byte-continuous,
   invisible to dash.js). Pure slowness never switches mirrors (backup URLs are for
   faults; speed is dash.js ABR's job).
5. **Re-fetch only on signature expiry**: CDN URLs are time-signed (~2h TTL,
   recognizing five param spellings: `expires|wsTime|txTime|um_deadline|deadline`).
   Playurl is re-fetched and retried only on a 403-class sweep or confirmed stamp
   expiry (same-stamp dedup); `?fresh=1` forces refresh for player self-rescue.
   **Slowness never re-fetches** — a fresh signature on the same bad edge changes nothing.

## Frontend

`static/vjs/dash.min.js` is vendored v5.2.1 (synced from the `dashjs` package via
`npm run sync:static`). **Player code may only use the v5 representations API** —
v4's `getBitrateInfoListFor` / `setQualityFor` are gone. `DashPlayerManager` lives
in `static/themes/modern/js/player.js`.

## durl-only fallback

Some uploads return only `durl` with no `dash` node under every endpoint/param
combination (verified 2026-09-11). `has_valid_dash_tracks()` is the gatekeeper:
without DASH tracks it switches to `fetch_durl_supported_src()` (non-WBI playurl
plus PGC retries, resolved in parallel), cached as `mikuinv_<vid>_<idx>_<qn>`
(`+_bak` for backups) and played through native `/proxy/video/...`. PGC durl
returns a single node per qn, so only exact matches are kept (otherwise the menu
shows one file under four names). **Progressive CDN fetches must use a web Chrome
UA** (`build_cdn_headers()` — BiliDroid UAs get 403s from upos/akamaized edges).

## Download jobs

`POST /download` opens a background job (max 3 concurrent, uuid as ID) and returns
`{"job_id"}`. The floating window in `static/themes/modern/js/download.js` polls
`GET /download/status/<job>` (progress, speed, phase:
queued/resolving/downloading/muxing/ready) and auto-saves
`GET /download/file/<job>` on completion. Cancel via `POST /download/cancel/<job>`
(beacon works too) — it drops the CDN connection, kills ffmpeg, and clears temp files.

Downloads share playback's mirror/watchdog machinery with **a more generous budget**
(8s handshake, full sweep, 5 backoff retries per segment): downloads have no client
intelligence, so the server must hold the connection itself. 32MB Range segments,
video+audio in parallel (mirroring dash.js's dual adaptation sets), 1MB batched
writes; start-mirror rotation per segment (sick ones sink, M-CDN last); adaptive
pacing (2/4/6/8/10s stepped backoff on sustained slowness — per-IP throttling
accumulates and decays, clean fast stretches reset to zero); keep-alive reuse;
current-speed slowdown watchdog (three consecutive 1s windows under 300KB/s after
previously proving ≥1MB/s → drop and reconnect after 10s — instant reconnects land
back in the penalty box). Playurl refreshes only on dead signatures (403 sweep or
stamp expiry), unlimited with same-stamp dedup.

Finished DASH downloads are muxed with `ffmpeg -c copy -movflags +faststart`
(ffmpeg ships in the app image); durl-only content is already a single MP4 and is
stored directly.

## Implementation constraints

- DASH CDN wants a web UA. Android app UAs get straight 403s.
- The `bili_ticket` cookie is never sent to web APIs (only the `x-bili-ticket`
  header on the CDN proxy) — otherwise the `v_voucher` pre-check fires.
- **Playback fails fast, downloads hold on — that's the design, not a bug.**
  Don't "unify" the two paths.
