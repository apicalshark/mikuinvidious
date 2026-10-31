# Bilibili Space Risk Control Architecture & Fingerprint Reverse Engineering Report

## 1. Overview

Bilibili's User Space (`https://space.bilibili.com/<mid>`) is among the most heavily risk-controlled read surfaces across the entire platform. When accessed from datacenter (IDC) IPs or clients failing device reputation checks, requests frequently encounter:
* **HTTP 412 Precondition Failed**: Immediate anti-bot gateway blockage.
* **API Response Code `-352` / `v_voucher`**: Triggers the Gaia risk control gateway, requiring interactive CAPTCHA verification.
* **Silent Truncation or Empty Collections**: Search/archive lists returning 0 videos or omitting critical fields.

This document details the live capture and reverse-engineering of `https://space.bilibili.com/177291194` (UID: 177291194, UP: DECO27_Official) using a headless Chromium browser driven by the **Chrome DevTools Protocol (CDP)**. It documents the 38 captured API endpoints, Gaia gateway handshakes, cookie token signing mechanisms, and the mathematical projections of the official `@bilibili/bili-user-fingerprint` SDK.

---

## 2. End-to-End Browser Call Flow & Protocol Matrix

When a browser loads a user space page, the frontend initiates the following sequence of calls:

```
[User Browser]
   │
   ├─► 1. Load static HTML & Bundles (index-*.js, bili-user-fingerprint.min.js)
   │
   ├─► 2. [Gaia Handshake] GET /x/internal/gaia-gateway/ExGetAxe
   │      └─► Server returns 2048-bit RSA public key and validity deadline
   │
   ├─► 3. [Gaia Telemetry] POST /x/internal/gaia-gateway/ExClimbCongLing
   │      └─► Client collects Canvas/WebGL/screen entropy, encrypts via AES,
   │          wraps AES key with server RSA public key, and POSTs payload
   │      └─► Server validates telemetry, sets bili-status-code: 0 and X-BILI-SEC-TOKEN cookie
   │
   ├─► 4. [User Info] GET /x/space/wbi/acc/info (WBI signed)
   │      └─► Attaches dm_img_* fingerprint and x-bili-device-req-json
   │
   └─► 5. [Video Search] GET /x/space/wbi/arc/search (WBI signed)
          └─► Attaches dm_img_*, order_avoided="true", index=1, etc.
```

---

## 3. Gaia Gateway & Security Token Architecture

### 3.1 `ExGetAxe` & `ExClimbCongLing` Handshake
1. **Public Key Retrieval**:
   * Request: `GET https://api.bilibili.com/x/internal/gaia-gateway/ExGetAxe`
   * Response:
     ```json
     {
       "code": 0,
       "message": "0",
       "data": {
         "version": "v1",
         "public_key": "-----BEGIN rsa public key-----\nMIICIjANBgkqhkiG9w0BAQEFAAOCAg8A...",
         "deadline": 2524608000
       }
     }
     ```
2. **Encrypted Telemetry Submission**:
   * Request: `POST https://api.bilibili.com/x/internal/gaia-gateway/ExClimbCongLing`
   * Payload Structure:
     ```json
     {
       "header": {
         "encode_type": 2,
         "payload_type": 4,
         "encoded_aes_key": "<RSA_Encrypted_AES_Key>",
         "ts": "<unix_epoch_ms>",
         "encoded_version": "v1"
       },
       "encrypt_payload": "<AES_CBC_Encrypted_Browser_Environment_Fingerprint>"
     }
     ```
   * Server Response Headers:
     ```http
     access-control-expose-headers: x-bili-gaia-vvoucher, x-bili-trace-id, x-bili-gaia-param
     bili-status-code: 0
     bili-trace-id: <trace_id>
     ```

### 3.2 `X-BILI-SEC-TOKEN` Security Cookie
Upon successful verification, the browser is issued an `X-BILI-SEC-TOKEN` cookie, which is a server-signed JSON Web Token (JWT):
```json
{
  "q": "<session_token_q>",
  "r": "<session_token_r>",
  "ip": "<client_egress_ip>",
  "fp": "<device_fingerprint_hash>",
  "verity": 1,
  "type": "1",
  "exp": 1793441706,
  "iat": 1793438103
}
```
* **Key Characteristic**: The security token cryptographically binds the client's **egress IP address** to the **device fingerprint hash (`fp`)** with `verity: 1`. If a request is received from a different IP or with divergent client hints / JA3 / HTTP/2 parameters, the token is invalidated, triggering `-352` / `v_voucher`.

---

## 4. Frontend Fingerprint Reverse Engineering (`bili-user-fingerprint`)

Analysis of `@bilibili/bili-user-fingerprint 1.1.4` (bundled in `bili-user-fingerprint.min.js`) revealed the exact implementation of `queryUserLog()`:

### 4.1 Base64 Trailing 2-Character Truncation (`_28b679`)
Official JavaScript code:
```javascript
var _28b679 = function(str) {
    var bytes = new TextEncoder().encode(str);
    var b64 = btoa(String.fromCharCode.apply(null, bytes));
    return b64.substring(0, b64.length - 2); // Strips trailing 2 characters!
};
```
* `dm_img_str`: `_28b679("WebGL 1.0 (OpenGL ES 2.0 Chromium)")`
  * Standard Base64: `V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ==`
  * Official Output (stripped `==`): `V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ`
* `dm_cover_img_str`: `_28b679(renderer_str)`, similarly trimmed by 2 characters.

### 4.2 Viewport Geometric Projection Formulas (`wh` & `of`)
Official projection matrix constants:
```javascript
var Mg = 2, Mw = 3, NA = 4, NQ = 5;

// Screen and Viewport Dimension Projection (wh):
var _186aea = function(w, h) {
    var rnd = Math.floor(114 * Math.random()); // Integer in [0, 113]
    return [
        2 * w + 2 * h + 3 * rnd,
        4 * w - h + rnd,
        rnd
    ];
};

// Scroll and Offset Dimension Projection (of):
var _580c77 = function(scrollLeft, scrollTop) {
    var rnd = Math.floor(514 * Math.random()); // Integer in [0, 513]
    return [
        3 * scrollLeft + 2 * scrollTop + rnd,
        4 * scrollLeft - 4 * scrollTop + 2 * rnd,
        rnd
    ];
};
```
* **Critical Finding**:
  On an un-scrolled page (`scrollLeft = 0, scrollTop = 0`):
  $$of = [rnd, 2 \times rnd, rnd]$$
  where $rnd \in [0, 513]$.
  In earlier backend implementations, sending static `of = [0, 0, 0]` corresponded to $rnd = 0$. In random sampling over $[0, 513]$, the probability of $0$ is only $\frac{1}{514}$. A persistent static `[0, 0, 0]` is an obvious signature easily flagged by anti-bot feature auditing.

---

## 5. Core API Parameter Reference

### 5.1 `/x/space/wbi/acc/info`
| Parameter | Type | Example Value | Description |
| :--- | :--- | :--- | :--- |
| `mid` | int / str | `177291194` | Target user UID |
| `token` | str | `""` | Space token, empty string by default |
| `platform` | str | `"web"` | Fixed to `web` |
| `web_location` | int / str | `1550101` | Space home page location code (fresh-space bundle) |
| `x-bili-device-req-json` | str (JSON) | `{"platform":"web","device":"pc","spmid":"333.1387","mobi_app":"web_i"}` | **Note**: Appended by frontend `METADATA` middleware as a URL parameter; included in WBI signature |
| `dm_img_list` | str (JSON) | `"[]"` | Image fingerprint array |
| `dm_img_str` | str | `V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ` | Base64 WebGL version trimmed by 2 characters |
| `dm_cover_img_str` | str | `QU5HTEUgKEdvb2dsZSwg...` | Base64 Renderer string trimmed by 2 characters |
| `dm_img_inter` | str (JSON) | `{"ds":[],"wh":[5847,6694,7],"of":[3,6,3]}` | Geometric projection; `of` is `[rnd, 2*rnd, rnd]` |
| `w_rid` | str | `<wbi_md5_hash>` | WBI Mixin Key MD5 hash |
| `wts` | int | `<unix_timestamp>` | Current Unix timestamp in seconds |

### 5.2 `/x/space/wbi/arc/search`
| Parameter | Type | Example Value | Description |
| :--- | :--- | :--- | :--- |
| `mid` | int / str | `177291194` | Target user UID |
| `order` | str | `"pubdate"` | Sort order (`pubdate` / `click` / `stow`) |
| `order_avoided` | str | `"true"` | **String type**, not boolean (boolean `True` encodes as `1` and fails) |
| `index` | int | `1` (or `0`) | Space home card index (`1` on home, `0` on video tab) |
| `ps` | int | `25` (or `30`) | Page size |
| `pn` | int | `1` | Page number |
| `tid` | int | `0` | Category ID |
| `keyword` | str | `""` | Search keyword |
| `special_type` | str | `""` | Special type |
| `platform` | str | `"web"` | Fixed to `web` |
| `web_location` | str | `"333.1387"` | Space video archive location code |
| `x-bili-device-req-json` | str (JSON) | `{"platform":"web","device":"pc","spmid":"333.1387","mobi_app":"web_i"}` | URL parameter, included in WBI signature |
| `dm_img_*` | str | Same as above | Follows `_28b679` 2-char trim and dynamic `of` |
| `w_rid`, `wts` | str, int | ... | WBI signature |

---

## 6. MikuInvidious Hardening Measures

Based on the reverse-engineering findings above, the following hardening measures have been implemented in the application:

1. **`dm_img_*` Fingerprints Aligned with Official SDK**:
   * In `python/api/video.py:_get_dm_img_params`, `dm_img_str` and `dm_cover_img_str` are trimmed by 2 characters (`[:-2]`), adhering to the official `_28b679` specification.
   * Replaced the static `of = [0, 0, 0]` with the official projection distribution:
     ```python
     rnd_of = random.randint(100, 450)
     of = [rnd_of, 2 * rnd_of, rnd_of]
     ```
2. **URL Query Parameters & WBI Signature Inclusion**:
   * In `python/api/user.py:get_user_info` and `get_videos`, `x-bili-device-req-json` is explicitly included in `params` so that it participates in parameter sorting and WBI hashing (`w_rid`), matching `fresh-space` bundle behavior.
3. **Multi-Tiered Degraded Resilience**:
   * Because datacenter IPs may face IP-level HTTP 412 gatekeeping regardless of fingerprint fidelity without residential proxies/WARP, the layered fallback mechanisms are preserved:
     * When `acc/info` is risk-controlled, seamlessly falls back to non-WBI `/x/web-interface/card`.
     * When `arc/search` is risk-controlled, seamlessly falls back to `/x/series/recArchivesByKeywords`.
