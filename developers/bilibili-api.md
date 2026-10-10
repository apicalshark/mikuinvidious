# Bilibili API wrapper

`python/api/`, own wrapper. Migrated from archived `bilibili-api-python`,
September 2026. Public symbols re-exported from `api/__init__.py`.

## Modules

| Module | Contents |
| :--- | :--- |
| `client.py` | HTTP client: WBI, `bili_ticket`, cookies, retries |
| `credential.py` | `Credential` auth class |
| `exceptions.py` | `ArgsException`, `ResponseCodeException` |
| `video.py` | video (info/tags/related/parts/cid/playurl/danmaku) |
| `user.py` | user (info/uploads/articles) |
| `search.py` | `search_by_type` + enums |
| `comment.py` | `get_comments` + enums |
| `live.py` | `LiveRoom`, `LiveDanmaku`, areas |
| `bangumi.py` | bangumi (metadata/episodes/index) |
| `audio.py` | `Audio`, `AudioList` |
| `article.py`, `opus.py` | articles / posts |
| `homepage.py`, `video_zone.py` | home / zone feeds |

## WBI signing

1. `GET /x/web-interface/nav` → `wbi_img.img_url`, `sub_url`.
2. Concatenate filenames, OE permutation table → 32-char `mixin_key`.
3. Each request adds `wts` (unix time); sorted params + urlencode + `mixin_key`,
   MD5 → `w_rid`.
4. On `-403`: drop key cache, re-fetch nav, re-sign.

## bili_ticket

1. `HMAC-SHA256(key="XgwSnGZ1p", msg=f"ts{int(time.time())}")`.
2. `POST /bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket` (hexsign, `key_id=ec02`).
3. Take `data.ticket`. Cache 3 days.

## Risk-control notes

- **Search, comments, `getInfoByRoom` need WBI + browser TLS.** curl_cffi Chrome
  impersonation (`_wbi_get`). Unsigned httpx → `-352`.
- **Playurl risk control = `code==0 + data.v_voucher`.**
  `Video._request_playurl` retries once via evasion channel (`isGaiaAvoided=true`,
  `gaia_source=pre-load`, `try_look=1`, `dm_img_*` fingerprints), then falls back
  to PGC.
- **Anonymous needs fingerprints for full quality.** `dm_img_*` WebGL templates,
  `web_location=1315873`, `x-bili-device-req-json` header. Fingerprint-less
  (`dm_img_switch=0`) caps anonymous at 480p. Static WBI backup key covers `/nav`
  outages.
- **UGC detail endpoints fake-404 PGC BVs.** Under risk control, `wbi/view` and
  `pagelist` return `-404` while PGC endpoints stay healthy. Playurl resolution must
  **never** let a UGC cid failure veto PGC: use a known `cid` (the `pgc_cid` from
  season lookup) directly; without one, hit PGC with `ep_id` alone.
- **Some non-WBI endpoints (`/x/web-interface/view`, danmaku, playurl) also 412 on
  datacenter IPs.** IP-level blocking, not signing. Route through WARP.
- **Space params must match the official bundle exactly.** `acc/info`:
  `{mid, token:"", platform:"web", web_location:1550101}`. `arc/search`:
  `{..., order_avoided:"true"` (string, not boolean), `platform:"web"`,
  `web_location:333.1387`, `special_type:""`, `index:0}` plus `dm_img_*`
  (`RISK_USER_LOG` middleware pattern; `dm_img_switch:"0"` without KvSDK).
  Fallback key is `orderby`, not `order`.
