# Bilibili Upstream API Reference & Signatures

This document catalogs the Bilibili API endpoints, parameters, and cryptographic signing schemes implemented across the local client module (`python/miku/api/`).

---

## 1. Upstream Endpoints

### Video (`api/video.py`)
- `GET https://api.bilibili.com/x/web-interface/view` — Basic video metadata (aid/bvid).
- `GET https://api.bilibili.com/x/web-interface/view/detail/tag` — Video tags.
- `GET https://api.bilibili.com/x/web-interface/archive/related` — Related video recommendations.
- `GET https://api.bilibili.com/x/player/pagelist` — Multi-part episode/page list.
- `GET https://api.bilibili.com/x/player/wbi/playurl` — **UGC DASH/Durl playurl (WBI signed).** Requires `web_location=1315873`, `fnval=4048`, `fourk=1`, and `x-bili-device-req-json` header.
- `GET https://api.bilibili.com/x/player/wbi/v2` — Subtitle and danmaku metadata (WBI signed).

### PGC & Bangumi (`api/bangumi.py`)
- `GET https://api.bilibili.com/pgc/view/web/season` — Season details, episode index, title.
- `GET https://api.bilibili.com/pgc/player/web/v2/playurl` — **PGC canonical playurl.** Accepts `ep_id` / `cid`.
- `GET https://api.bilibili.com/pgc/web/season/section` — Extra clips and PV sections.
- `GET https://api.bilibili.com/pgc/season/index/result` — Bangumi catalog filter.

### User & Space (`api/user.py`)
- `GET https://api.bilibili.com/x/space/wbi/acc/info` — User profile (WBI signed).
  - *Risk Fallback:* Falls back to `GET https://api.bilibili.com/x/web-interface/card` if 412 / `-352` occurs.
- `GET https://api.bilibili.com/x/space/wbi/arc/search` — Video archives (WBI signed, with `order_avoided="true"`).
  - *Risk Fallback:* Falls back to `GET https://api.bilibili.com/x/series/recArchivesByKeywords` with `dm_img_*` fingerprints.
- `GET https://api.bilibili.com/x/space/wbi/article` — User articles (WBI signed).

### Live Streaming (`api/live.py`, `api/live_area.py`)
- `GET https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom` — Room details. Requires WBI signing with `web_location=444.8` and browser TLS impersonation via `curl_cffi`.
- `GET https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo` — Stream format choices (FLV, HLS, codecs).
- `GET https://api.live.bilibili.com/xlive/web-room/v1/playUrl/playUrl` — Live stream master URL.
- `GET https://api.live.bilibili.com/xlive/web-interface/v1/second/getList` — Sub-area live taxonomy and tags.

### Search & Discussion (`api/search.py`, `api/comment.py`)
- `GET https://api.bilibili.com/x/web-interface/wbi/search/type` — Category search (WBI signed).
- `GET https://api.bilibili.com/x/v2/reply/main` — Video and article comment threads.

### Audio, Articles & Feeds (`api/audio.py`, `api/article.py`, `api/opus.py`, `api/homepage.py`, `api/video_zone.py`)
- `GET https://www.bilibili.com/audio/music-service-c/web/song/info` — Song metadata.
- `GET https://www.bilibili.com/audio/music-service-c/web/url` — Audio track stream URL.
- `GET https://www.bilibili.com/audio/music-service-c/web/menu/info` — Playlist metadata.
- `GET https://www.bilibili.com/audio/music-service-c/web/song/of-menu` — Songs in playlist.
- `GET https://api.bilibili.com/x/article/viewinfo` — Supplementary article metadata (stats: likes, coins, favorites; fallback title). The article body itself is parsed directly from public web HTML by `miku/views/space.py`.
- `GET https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail` — Opus post content.
- `GET https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd` — Homepage recommendations (WBI signed).
- `GET https://api.bilibili.com/x/web-interface/dynamic/region` — Sub-zone dynamic feed.

---

## 2. Signing Schemes & Security Tokens

### WBI Query Signing (`api/client.py`)
WBI is Bilibili's primary API signature mechanism:
1. **Key Extraction:** Retrieve `/x/web-interface/nav`. Extract `wbi_img.img_url` and `wbi_img.sub_url`.
2. **Mixin Key Derivation:** Concatenate filename basenames and apply the Bilibili OE permutation table to produce a 32-character `mixin_key`.
3. **Query Signing:**
   - Ensure `web_location` defaults to `1315873` if absent or None prior to sorting.
   - Append `wts=<unix_timestamp>`.
   - Sort query parameters alphabetically by key.
   - URL-encode each key and value using percent-encoding with spaces encoded as `%20` (never `+`, which produces an invalid signature).
   - Concatenate query pairs with `&` and append the `mixin_key`.
   - Compute `MD5` checksum to generate `w_rid`.
4. **Invalidation:** If the upstream JSON response payload contains `"code": -403` (distinct from an HTTP 403 status code), invalidate the cached `mixin_key`, re-fetch fresh keys from `/x/web-interface/nav`, and retry.

### `bili_ticket` Token Generation
A cryptographic proof header required for CDN media access:
1. Compute `HMAC-SHA256(key="XgwSnGZ1p", message=f"ts{int(time.time())}")`.
2. `POST https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket` with POST parameters:
   - `key_id: "ec02"`
   - `hexsign: <hex_signature>`
   - `context[ts]: "<timestamp>"` (matching the UNIX timestamp in the HMAC signature)
   - `csrf: ""`
3. Extract `data.ticket` and cache for up to 3 days (or as specified by `ttl`).
4. **Header Rule:** Send *only* via the `x-bili-ticket` HTTP request header for CDN proxying. Never send as a browser cookie (sending as cookie triggers empty anti-bot search results).

### Gaia Risk Avoidance (`api/video.py`, `api/user.py`)
Upstream bot detection gates requests via captcha challenges (`v_voucher`) or `-352`/`412` risk codes. MikuInvidious mitigates these through lane-specific fallbacks:
- **Playurl Avoidance Lane:** When a playurl endpoint returns `v_voucher` or `-352`, `api/video.py` retries once with fresh WebGL/canvas fingerprint parameters (`dm_img_list`, `dm_img_str`, `dm_cover_img_str`, `dm_img_inter`) and `gaia_source=pre-load`, `isGaiaAvoided=true`, and `try_look=1`.
- **Space Route Fallbacks:** `api/user.py` falls back from `/x/space/wbi/acc/info` to `/x/web-interface/card` on errors or when neither `name` nor `face` is present, and from `/x/space/wbi/arc/search` to `/x/series/recArchivesByKeywords` on errors or when `list.vlist` is empty or absent. Missing expected fields can trigger these fallbacks even without an explicit risk code.
- **Operational Reality:** These retries and alternative endpoints do not guarantee a risk-control bypass or the requested endpoint data. Fallback responses may be incomplete or empty. An upstream proxy or residential/WARP egress via `proxy_url` may help with datacenter IP blocks but does not guarantee success.