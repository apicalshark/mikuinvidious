# Bilibili API wrapper

`python/api/` is our own wrapper, migrated from the archived `bilibili-api-python`
in September 2026. Public symbols are all re-exported from `api/__init__.py`.

## Modules

| Module | Contents |
| :--- | :--- |
| `client.py` | HTTP client: WBI signing, `bili_ticket`, automatic cookies, retries |
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

1. `GET /x/web-interface/nav` for `wbi_img.img_url` and `sub_url`.
2. Concatenate the filenames and run them through the OE permutation table to get
   the 32-char `mixin_key`.
3. Every request adds `wts` (unix time); params are sorted, urlencoded, suffixed
   with `mixin_key`, and MD5'd into `w_rid`.
4. On `-403`, drop the key cache, re-fetch nav, re-sign.

## bili_ticket

1. Compute `HMAC-SHA256(key="XgwSnGZ1p", msg=f"ts{int(time.time())}")`.
2. `POST /bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket` (hexsign + `key_id=ec02`).
3. Take `data.ticket`, cache for 3 days.

## Risk-control field notes

- **Search, comments, and `getInfoByRoom` need WBI + browser TLS**: these use curl_cffi
  Chrome impersonation (`_wbi_get`). Unsigned httpx requests get `-352`.
- **Playurl risk control reads as `code==0 + data.v_voucher`**:
  `Video._request_playurl` retries once through the evasion channel
  (`isGaiaAvoided=true`, `gaia_source=pre-load`, `try_look=1`, `dm_img_*` fingerprints);
  if still blocked, it falls back to PGC.
- **Anonymous access needs fingerprints for the full quality ladder**: `dm_img_*`
  WebGL template fingerprints, `web_location=1315873`, and the
  `x-bili-device-req-json` header. Fingerprint-less mode (`dm_img_switch=0`) caps
  anonymous quality at 480p. A static WBI backup key covers `/nav` outages.
- **UGC detail endpoints fake-404 PGC BVs** (under risk control, `wbi/view` and
  `pagelist` return `-404` while PGC endpoints stay healthy): playurl resolution
  must **never** let a UGC cid failure veto the PGC path — use a known `cid` (the
  `pgc_cid` from season lookup) directly; without a cid, hit PGC with just `ep_id`.
- **Some non-WBI endpoints (`/x/web-interface/view`, danmaku, playurl) also 412 on
  datacenter IPs**: that's IP-level blocking, not a signing problem — route through
  WARP.
- **Space params must match the official bundle exactly**: `acc/info` is
  `{mid, token:"", platform:"web", web_location:1550101}`; `arc/search` is
  `{..., order_avoided:"true"` (string, not boolean), `platform:"web"`,
  `web_location:333.1387`, `special_type:""`, `index:0}` plus `dm_img_*`
  (`RISK_USER_LOG` middleware pattern, `dm_img_switch:"0"` without KvSDK). The
  fallback key is `orderby`, not `order`.
