"""Unified Client Fingerprint & Request Headers Management.

This module acts as the single source of truth for all HTTP request headers,
User-Agent strings, and client fingerprinting configurations across MikuInvidious.

Background & Anti-Bot Architecture:
-----------------------------------
Bilibili operates distinct anti-scraping and risk-control mechanisms across
different endpoints and service layers:

1. High-Risk Web APIs (Live room info, Search, Comments, User Space):
   - Protected by Gaia risk control (-352 / code: 0 with v_voucher).
   - Requires curl_cffi with full browser TLS impersonation (JA3/JA4/HTTP2 settings).
   - MUST maintain exact alignment between the TLS impersonation target (`chrome152`),
     the User-Agent string (`Chrome/152.0.0.0`), and Client Hints (`sec-ch-ua`).
   - Insertion order of header names is load-bearing because HTTP/2 pseudo-header
     and header ordering form part of the browser fingerprint.
   - UA randomization is strictly prohibited as it triggers immediate risk control.

2. Media CDN Edges (.bilivideo.com, upos mirrors, akamaized):
   - Deliberately do NOT use TLS impersonation, as raw sockets (CdnConnection)
     are required for WARP SOCKS5 tunneling, HTTP Range resumes, and throughput watchdogs.
   - The CDN validates the User-Agent *string* and signed URL token.
   - Crucial Gotcha: Bilibili's media CDN returns HTTP 403 Forbidden when requested
     with Android app UAs (`BiliDroid/...`). Therefore, all VOD (DASH tracks and
     progressive MP4) requests MUST carry a Web Chrome User-Agent.

3. App Mobile API & Fallback Paths:
   - Android App UA (`BiliDroid/8.83.0`) is used for image proxying and live FLV stream
     forwarding.
   - App UA is used for scraping public articles
     (/read/ and /opus/) where datacenter IPs are often stripped of content modules on web views.
   - The App UA is configurable via `config.toml` (`[bili].user_agent`) or `BILI_USER_AGENT`.
"""

from __future__ import annotations

__all__ = [
    "APP_UA",
    "DEFAULT_APP_UA",
    "DEFAULT_APP_REFERER",
    "DEFAULT_APP_ENV",
    "DEFAULT_APP_KEY",
    "DEFAULT_APP_REGION",
    "ARTICLE_SCRAPE_UA",
    "CHROME_VERSION",
    "CHROME_UA",
    "CURL_IMPERSONATE",
    "CURL_UA",
    "CDN_CHROME_UA",
    "FIXED_CHROME_UA",
    "get_app_headers",
    "get_browser_headers",
    "get_cdn_headers",
    "get_web_api_headers",
    "get_article_scrape_headers",
]

# ---------------------------------------------------------------------------
# 1. Unified App Profile (BiliDroid 8.83.0)
# ---------------------------------------------------------------------------
DEFAULT_APP_UA = (
    "Mozilla/5.0 BiliDroid/8.83.0 (bbcallen@gmail.com) 8.83.0 "
    "os/android model/MI 9 mobi_app/android build/8830500 channel/html5_search_google "
    "innerVer/8830510 osVer/13 network/2"
)
APP_UA = DEFAULT_APP_UA
ARTICLE_SCRAPE_UA = DEFAULT_APP_UA
DEFAULT_APP_REFERER = "https://www.bilibili.com"
DEFAULT_APP_ENV = "prod"
DEFAULT_APP_KEY = "android64"
DEFAULT_APP_REGION = "CN"


def get_app_headers(bili_conf: dict | None = None) -> dict[str, str]:
    """Get common headers for Bilibili App / mobile requests.

    Allows overriding via `bili_conf` (from app configuration).
    """
    conf = bili_conf or {}
    return {
        "User-Agent": conf.get("user_agent", DEFAULT_APP_UA),
        "Referer": conf.get("referer", DEFAULT_APP_REFERER),
        "env": conf.get("env", DEFAULT_APP_ENV),
        "app-key": conf.get("app_key", DEFAULT_APP_KEY),
        "x-bili-metadata-ip-region": conf.get("ip_region", DEFAULT_APP_REGION),
        "x-bili-metadata-legal-region": conf.get("legal_region", DEFAULT_APP_REGION),
    }


def get_article_scrape_headers(bili_conf: dict | None = None) -> dict[str, str]:
    """Headers for scraping public read/opus articles."""
    conf = bili_conf or {}
    return {
        "User-Agent": conf.get("user_agent", DEFAULT_APP_UA),
    }


# ---------------------------------------------------------------------------
# 2. Unified Chrome Specification (curl_cffi chrome152 / Chrome 152)
# ---------------------------------------------------------------------------
# Pinned to the latest Chrome target supported by curl_cffi (0.17.0+): chrome152.
# Unifies browser impersonation, raw-socket CDN media fetches, and Web API headers.
CHROME_VERSION = "152"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)

# Impersonation configuration for curl_cffi
# Header NAME ORDER is part of the HTTP/2 fingerprint, so insertion order
# is load-bearing — do not re-sort.
CURL_IMPERSONATE = "chrome152"
CURL_UA = CHROME_UA
_CURL_SEC_CH_UA = '"Chromium";v="152", "Not-A.Brand";v="24", "Google Chrome";v="152"'
_CURL_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7,ja;q=0.6"


def get_browser_headers(
    *,
    origin: str,
    referer: str,
    accept: str = "application/json, text/plain, */*",
) -> dict[str, str]:
    """Ordered Chrome XHR headers for a curl_cffi request.

    Must be sent with ``default_headers=False`` so curl does not append its
    own ``origin``/``referer``/``accept`` at the tail (which breaks the
    browser order).
    """
    return {
        "sec-ch-ua": _CURL_SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": CURL_UA,
        "accept": accept,
        "origin": origin,
        "sec-fetch-site": "same-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "referer": referer,
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": _CURL_ACCEPT_LANGUAGE,
        "priority": "u=1, i",
    }


# ---------------------------------------------------------------------------
# 3. Media CDN Profile (Raw-Socket CdnConnection / Chrome 152)
# ---------------------------------------------------------------------------
# Web-Chrome UA string for raw-socket CDN fetches. Deliberately NOT the
# impersonation stack. Media edges validate the UA string + signed token,
# not the TLS fingerprint. Unified with CHROME_UA.
CDN_CHROME_UA = CHROME_UA


def get_cdn_headers(referer: str = "https://www.bilibili.com") -> dict[str, str]:
    """Base header set for raw-socket CDN media requests (proxy/dash paths)."""
    return {
        "User-Agent": CDN_CHROME_UA,
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
        "Accept": "*/*",
    }


# ---------------------------------------------------------------------------
# 4. Web API General Profile (Chrome 152)
# ---------------------------------------------------------------------------
# Fixed Chrome UA for standard web API endpoints and WebSocket handshakes.
# Do not randomize. Unified with CHROME_UA.
FIXED_CHROME_UA = CHROME_UA


def get_web_api_headers(referer: str = "https://www.bilibili.com") -> dict[str, str]:
    """General web API headers dictionary."""
    return {
        "User-Agent": FIXED_CHROME_UA,
        "Referer": referer,
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
