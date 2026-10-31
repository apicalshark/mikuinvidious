# Client Fingerprints & User-Agent Management

All client fingerprints, HTTP User-Agent strings, and request headers in MikuInvidious are centralized in [`python/fingerprints.py`](../python/fingerprints.py).

This document explains the architecture, the rationale behind each profile, and verified anti-bot gotchas.

---

## 1. Overview & Rationale

Bilibili enforces strict, heterogeneous anti-bot and risk-control mechanisms across different service endpoints:
- **API endpoints** check for Wbi signatures, Gaia risk control (`-352`), ticket cookies, and TLS/HTTP2 fingerprints.
- **Media CDN nodes** validate URL token signatures and client User-Agent strings, but do not validate TLS fingerprints.
- **Article/Dynamic scraping** from datacenter IPs is subjected to content-stripping when requested via desktop browsers.

Randomizing User-Agents or using mismatched browser versions triggers immediate anti-bot blocks (`-352` or HTTP 412). To prevent version drift and conflicting headers, all request headers are constructed through unified profiles.

---

## 2. Fingerprint Profiles Matrix

| Profile | User-Agent / Identity | Target Endpoints / Layers | Transport Layer | Key Headers & Attributes |
| :--- | :--- | :--- | :--- | :--- |
| **App Profile** (`get_app_headers`) | `BiliDroid/8.83.0` (Android) | Image proxying (`/proxy/image/`), Live FLV stream forward | `httpx` AsyncClient / Raw socket | `app-key: android64`, `env: prod`, metadata IP/legal region. Configurable via `config.toml`. |
| **Browser Impersonation** (`get_browser_headers`) | `Chrome/152.0.0.0` (Windows) | High-risk APIs: Live `getInfoByRoom`, Search, Comments, User Space | `curl_cffi` (`CURL_IMPERSONATE = "chrome152"`) | Strict insertion order for HTTP/2 fingerprinting; `sec-ch-ua` (v152), `sec-fetch-*`, `priority`. Sent with `default_headers=False`. |
| **Media CDN Profile** (`get_cdn_headers`) | `Chrome/152.0.0.0` (Windows) | DASH segments & progressive MP4 CDN fetches (`.bilivideo.com`, upos mirrors) | Raw socket (`CdnConnection`) | `Referer`, `Origin: https://www.bilibili.com`, `Accept: */*`. Validates UA string; bypassing App UA 403. |
| **General Web API** (`get_web_api_headers`) | `Chrome/152.0.0.0` (Windows) | Normal Web APIs (Wbi nav key, subtitles, video info, WebSocket live danmaku) | `httpx` AsyncClient / `aiohttp` | Fixed Chrome UA, `Accept-Language: zh-CN,zh;q=0.9`. |
| **Article Scrape** (`get_article_scrape_headers`) | `BiliDroid/8.83.0` (Android) | Fallback scraper for `/read/` and `/opus/` articles | `httpx` AsyncClient | Unified mobile app UA (`APP_UA`) used to avoid datacenter IP content-stripping on web views. Configurable via `config.toml`. |

---

## 3. Verified Gotchas & Invariants

### A. Media CDN Requires Web User-Agent (HTTP 403 on BiliDroid)
* **Gotcha**: Requesting video streams (`.bilivideo.com`, upos mirrors, akamaized) with an Android app User-Agent (`BiliDroid/...`) returns **HTTP 403 Forbidden**.
* **Invariant**: All DASH and progressive VOD proxying/downloading (`proxy_dash`, `proxy_video`, `dash_download_track`) **must** use `get_cdn_headers()` and must never inherit `get_app_headers()`.

### B. TLS Fingerprint & Client Hints Must Align (Anti-Bot `-352`)
* **Gotcha**: On endpoints protected by Gaia risk control (e.g. `getInfoByRoom`), Bilibili compares the HTTP/2 settings, TLS fingerprint (JA3/JA4), `user-agent`, and `sec-ch-ua` Client Hints. If the TLS impersonation target is Chrome 152 but the User-Agent claims to be a different browser version or Firefox, the request is flagged as an automated bot and rejected with `-352`.
* **Invariant**: In `get_browser_headers()`, `CURL_IMPERSONATE = "chrome152"`, `CURL_UA`, and `sec-ch-ua` (v152) are unified and pinned together, and the dictionary insertion order must be strictly preserved.

### C. Never Randomize User-Agents
* **Gotcha**: Dynamically rotating User-Agent strings across requests trips rate limits and risk control immediately.
* **Invariant**: User-Agents are strictly deterministic constants.

---

## 4. Configuration

Only the **App Profile** (`BiliDroid`) can be overridden by users in `config.toml` or environment variables:

```toml
[bili]
# Optional override for the mobile App User-Agent
user_agent = "Mozilla/5.0 BiliDroid/8.83.0 (bbcallen@gmail.com) 8.83.0 os/android model/MI 9 mobi_app/android build/8830500 channel/html5_search_google innerVer/8830510 osVer/13 network/2"
```

Environment variable alternative:
```bash
export BILI_USER_AGENT="Mozilla/5.0 BiliDroid/..."
```

Browser and CDN fingerprints are intentionally managed internally by [`python/fingerprints.py`](../python/fingerprints.py) to prevent misconfiguration that could break playback or trigger risk controls.

## 4. Space & Gaia Risk Control Architecture

For detailed reverse-engineering findings on Space endpoints (`/x/space/wbi/acc/info`, `/x/space/wbi/arc/search`), Gaia gateway handshakes (`ExGetAxe` / `ExClimbCongLing`), the `X-BILI-SEC-TOKEN` cookie, and `@bilibili/bili-user-fingerprint` projection formulas (`dm_img_inter`), see [`doc/space_risk_control.md`](space_risk_control.md).
