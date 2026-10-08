# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.
"""
Low-level Bilibili HTTP client with Wbi signing, bili_ticket, auto-cookies
and response processing.

Minimal drop-in for ``bilibili_api.utils.network`` covering only the
subset this project uses.
"""

import asyncio
import base64
import hashlib
import hmac
import random
import time
import urllib.parse

import httpx

from .credential import Credential
from .exceptions import ArgsException, NetworkException, ResponseCodeException

__all__ = [
    "Api",
    "get_bili_client",
    "get_curl_client",
    "request_settings",
    "get_bili_ticket",
    "refresh_bili_ticket",
    "get_wbi_mixin_key",
    "recalculate_wbi",
    "HEADERS",
    "FIXED_CHROME_UA",
    "CURL_IMPERSONATE",
    "CURL_UA",
    "CDN_CHROME_UA",
    "build_chrome_headers",
    "build_cdn_headers",
]

# Fixed UA — do not randomize (triggers risk control).
FIXED_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


HEADERS: dict[str, str] = {
    "User-Agent": FIXED_CHROME_UA,
    "Referer": "https://www.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ---------------------------------------------------------------------------
# Browser-faithful Chrome headers for curl_cffi paths (search/comment/live).
# ---------------------------------------------------------------------------
# Ported from BilibiliApis builder/header.py (see doc/bili-research-docs/).
# Verified live Sep 2026: getInfoByRoom returned -352 with a minimal 3-header
# set and 0 with this full ordered set, all else (params, signing, cookies)
# identical. Header NAME ORDER is part of the HTTP/2 fingerprint, so the
# insertion order below is load-bearing — do not re-sort.
#
# UA/client-hints must match the impersonation target or UA and TLS
# fingerprint contradict each other (same reason FIXED_CHROME_UA exists).
CURL_IMPERSONATE = "chrome150"
CURL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
_CURL_SEC_CH_UA = '"Chromium";v="150", "Not-A.Brand";v="24", "Google Chrome";v="150"'
_CURL_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7,ja;q=0.6"


def build_chrome_headers(
    *,
    origin: str,
    referer: str,
    accept: str = "application/json, text/plain, */*",
) -> dict[str, str]:
    """Ordered Chrome XHR headers for a curl_cffi request.

    Must be sent with ``default_headers=False`` so curl does not append its
    own ``origin``/``referer``/``accept`` at the tail (which breaks the
    browser order). ``origin``/``referer`` identify the calling page:
    live room page for live, search page for search, main site otherwise.
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


# Web-Chrome UA string for raw-socket CDN fetches (CdnConnection). Deliberately
# NOT the impersonation stack: media edges (upos mirrors, akamaized,
# .bilivideo.com) validate the UA *string* plus the signed URL token — never
# the TLS fingerprint (proven live: plain-socket 206s with this string, 403s
# with the BiliDroid app UA). httpx-curl-cffi is an httpx *transport* and
# cannot drive our socket layer (WARP SOCKS5, Range-resume, mid-body watchdog),
# so CDN paths carry this string while API paths carry full impersonation.
# Version intentionally differs from CURL_UA (150, pinned to the impersonation
# target): CDN edges only check "browser-like", and 152 is battle-tested here.
CDN_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


def build_cdn_headers(referer: str = "https://www.bilibili.com") -> dict[str, str]:
    """Base header set for raw-socket CDN media requests (proxy/dash paths)."""
    return {
        "User-Agent": CDN_CHROME_UA,
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
        "Accept": "*/*",
    }

# OE permutation table used to derive the wbi mixin key.
OE = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]

_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
_TICKET_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
_SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"

# Static fallback WBI keys embedded in the web bundles (space fresh-space
# index-*.js `encWbiKeys`, fetched Oct 2026). Used only when
# `/x/web-interface/nav` is unreachable so wbi-signed requests degrade
# gracefully instead of failing.
# Previous pair (player_core.*.js `w()`/`C()`, verified Sep 29 2026 against
# core.ba67b466.js) kept for reference / instant revert:
#   _FALLBACK_WBI_IMG_KEY = "5a6f002d0bb14fc9848fc64157648ad4"
#   _FALLBACK_WBI_SUB_KEY = "0503a77b29d7409d9548fb44fe9daa1a"
_FALLBACK_WBI_IMG_KEY = "4a1d4479a1ea4146bc7552eea71c28e9"
_FALLBACK_WBI_SUB_KEY = "fa5812e23a204d10b332dc24d992432d"

# `x-bili-device-req-json` header the web player attaches to every unified
# request (player_core.*.js `r0` middleware). `mobi_app` is UA-parsed client-side;
# plain web browsers send "web".
_DEVICE_REQ_JSON = '{"platform":"web","device":"pc","mobi_app":"web"}'

# Wbi mixin key cache
__wbi_mixin_key = ""
__wbi_lock = asyncio.Lock()
# bili_ticket cache
__bili_ticket = ""
__bili_ticket_expires = 0

# Global request settings (proxy). Note: single leading underscore to avoid
# Python's private name mangling inside RequestSettings methods.
_proxy = ""


class RequestSettings:
    """Global mutable request settings (mainly proxy routing)."""

    def set_proxy(self, proxy: str):
        global _proxy
        _proxy = proxy

    def get_proxy(self) -> str:
        global _proxy
        return _proxy

    def set(self, key, value):
        if key == "proxy":
            self.set_proxy(value)

    def get(self, key, default=None):
        if key == "proxy":
            return self.get_proxy()
        return default


request_settings = RequestSettings()

# Process-wide single client. ALL Bilibili web-API traffic goes through the
# Chrome-impersonating transport (httpx API over curl-impersonate): a single
# uniform TLS/JA3 fingerprint is itself a trust signal — a real browser never
# shows two different fingerprints from one device. The old native-httpx
# client is gone; streaming media still uses raw sockets (proxy.py /
# dash_proxy.py) and never touches this path (the curl transport buffers
# request bodies in memory and ignores write timeouts, so it must stay away
# from multi-hundred-MB streams and hour-long reads).
__client = None
__client_configured_proxy = None
__client_lock = asyncio.Lock()


def _build_curl_transport(proxy):
    from httpx_curl_cffi import AsyncCurlTransport

    return AsyncCurlTransport(
        impersonate=CURL_IMPERSONATE,
        default_headers=False,
        proxy=proxy,
    )


async def get_bili_client() -> httpx.AsyncClient:
    """Return the shared async client (recreated when proxy changes)."""
    global __client, __client_configured_proxy
    proxy = request_settings.get_proxy() or None
    if __client is None or __client.is_closed or __client_configured_proxy != proxy:
        async with __client_lock:
            proxy = request_settings.get_proxy() or None
            if __client is None or __client.is_closed or __client_configured_proxy != proxy:
                if __client is not None and not __client.is_closed:
                    await __client.aclose()
                __client = httpx.AsyncClient(
                    transport=_build_curl_transport(proxy),
                    trust_env=False,
                    timeout=httpx.Timeout(None, connect=15.0, pool=30.0, read=30.0),
                    follow_redirects=False,
                )
                # httpx seeds 4 client-level defaults (accept/accept-encoding/
                # connection/user-agent) via the headers *setter* and merges
                # them ahead of per-request headers — including
                # `connection: keep-alive`, which never appears on real
                # HTTP/2 traffic and breaks our ordered Chrome set. Bypass
                # the setter so requests carry exactly what callers pass
                # (plus the transport-level pseudo-headers curl manages).
                __client._headers = httpx.Headers()
                __client_configured_proxy = proxy
    return __client


async def get_curl_client() -> httpx.AsyncClient:
    """Alias: the impersonating client is now the only client (see above)."""
    return await get_bili_client()


# ---------------------------------------------------------------------------
# Wbi signing
# ---------------------------------------------------------------------------


async def _get_mixin_key(credential: Credential = None) -> str:
    credential = credential or Credential()
    client = await get_bili_client()
    resp = await client.get(
        _NAV_URL,
        headers=HEADERS,
        cookies=credential.get_cookies(),
    )
    data = resp.json().get("data") or {}
    wbi_img = data.get("wbi_img") or {}

    def split(key):
        url = (wbi_img.get(key) or "").split("/")[-1].split(".")[0]
        return url

    ae = split("img_url") + split("sub_url")
    le = []
    for i in OE:
        if i < len(ae):
            le.append(ae[i])
    return "".join(le)[:32]


def _mixin_from_keys(img_key: str, sub_key: str) -> str:
    """Derive the 32-char wbi mixin key from raw img/sub key filenames."""
    ae = img_key + sub_key
    return "".join(ae[i] for i in OE if i < len(ae))[:32]


def _enc_wbi(params: dict, mixin_key: str) -> dict:
    params.pop("w_rid", None)
    params["wts"] = int(time.time())
    if params.get("web_location") is None:
        # The web player forces web_location=1315873 on the playurl path
        # (player_core.*.js `I()`); keep it as the global default for parity.
        params["web_location"] = 1315873
    # Match PipePipe / bilibili-API-collect: sort keys, then percent-encode each
    # pair with %20 (NOT the '+' that urllib.urlencode uses, which produces an
    # invalid wbi signature whenever a value contains a space).
    query = "&".join(
        f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(params.items())
    )
    params["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return params


async def get_wbi_mixin_key(credential: Credential = None) -> str:
    global __wbi_mixin_key
    if __wbi_mixin_key == "":
        async with __wbi_lock:
            if __wbi_mixin_key == "":
                try:
                    key = await _get_mixin_key(credential)
                except Exception as exc:
                    key = ""
                    print(f"[WBI] nav failed ({exc}); using static player fallback keys")
                if not key:
                    key = _mixin_from_keys(_FALLBACK_WBI_IMG_KEY, _FALLBACK_WBI_SUB_KEY)
                __wbi_mixin_key = key
    return __wbi_mixin_key


def recalculate_wbi():
    global __wbi_mixin_key
    __wbi_mixin_key = ""


# ---------------------------------------------------------------------------
# bili_ticket
# ---------------------------------------------------------------------------


def _hmac_sha256(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


async def _get_bili_ticket(credential: Credential = None) -> str:
    credential = credential or Credential()
    client = await get_bili_client()
    o = _hmac_sha256("XgwSnGZ1p", f"ts{int(time.time())}")
    params = {
        "key_id": "ec02",
        "hexsign": o,
        "context[ts]": str(int(time.time())),
        "csrf": "",
    }
    resp = await client.post(
        _TICKET_URL,
        params=params,
        headers=HEADERS,
        cookies=credential.get_cookies(),
    )
    return resp.json()["data"]["ticket"]


async def get_bili_ticket(credential: Credential = None):
    """Return (ticket, expiry_ts). Clears and regenerates when expired."""
    global __bili_ticket, __bili_ticket_expires
    if __bili_ticket == "" or time.time() > int(__bili_ticket_expires):
        __bili_ticket = await _get_bili_ticket(credential)
        __bili_ticket_expires = str(int(time.time()) + 3 * 86400)
    return __bili_ticket, __bili_ticket_expires


def refresh_bili_ticket():
    """Clear the cached bili_ticket so the next request regenerates it."""
    global __bili_ticket, __bili_ticket_expires
    __bili_ticket = ""
    __bili_ticket_expires = 0


# ---------------------------------------------------------------------------
# Auto buvid
# ---------------------------------------------------------------------------


async def _get_buvid():
    client = await get_bili_client()
    resp = await client.get(_SPI_URL, headers=HEADERS)
    data = resp.json().get("data") or {}
    return data.get("b_3", ""), data.get("b_4", "")


# ---------------------------------------------------------------------------
# Full anonymous cookie set (buvid3, buvid4, bili_ticket, b_nut, b_lsid, _uuid, buvid_fp)
# ---------------------------------------------------------------------------

_anonymous_cookies: dict[str, str] = {}
_anonymous_cookies_expires: float = 0
_anonymous_cookies_lock = asyncio.Lock()
# Future shared while a cookie refresh is running (singleflight: one
# SPI + GenWebTicket round-trip no matter how many callers miss at once).
_anonymous_cookies_inflight = None


def _generate_uuid() -> str:
    """Generate a fake UUID in Bilibili's format (XXXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX00000infoc)."""
    hex_chars = "0123456789ABCDEF"
    parts: list[str] = []
    for i in range(32):
        if i in (9, 13, 17, 21):
            parts.append("-")
        parts.append(random.choice(hex_chars))
    t = int(time.time() * 1000) % 100000
    return "".join(parts) + f"{t:05d}infoc"


def _generate_b_lsid() -> str:
    """Generate a random b_lsid cookie value."""
    hex_chars = "0123456789ABCDEF"
    rand_part = "".join(random.choice(hex_chars) for _ in range(32))
    ts_part = f"{int(time.time() * 1000):X}"
    return f"{rand_part}_{ts_part}"


async def _fetch_anonymous_cookies(now: float) -> tuple[dict[str, str], float]:
    """Build the anonymous cookie set. Runs outside the lock; never raises.

    Returns ``(cookies, expires_ts)``. Reuses the pooled client instead of
    spawning a fresh one per call.
    """
    cookies: dict[str, str] = {}
    client = await get_bili_client()

    # Step 1: Fetch buvid3/buvid4 from /x/frontend/finger/spi
    try:
        resp = await client.get(_SPI_URL, headers=HEADERS)
        spi = resp.json().get("data") or {}
        cookies["buvid3"] = spi.get("b_3", "")
        cookies["buvid4"] = spi.get("b_4", "")
    except Exception:
        cookies["buvid3"] = ""
        cookies["buvid4"] = ""

    # Step 2: Generate local cookies
    cookies["b_nut"] = str(int(now))
    cookies["b_lsid"] = _generate_b_lsid()
    cookies["_uuid"] = _generate_uuid()
    cookies["buvid_fp"] = "".join(random.choice("0123456789abcdef") for _ in range(32))

    # Step 3: Generate bili_ticket via HMAC-SHA256 -> GenWebTicket.
    # NOTE: we do NOT send the bili_ticket as a cookie to Bilibili's web API.
    # Sending it triggers Bilibili's anti-bot "v_voucher" precheck, which returns
    # an empty result (e.g. numResults=None) for wbi endpoints like search. This
    # matches upstream bilibili-api-python's default (enable_bili_ticket=False),
    # which never sent a bili_ticket cookie. The x-bili-ticket HEADER (for CDN /
    # DASH proxy) is managed separately by shared.TicketManager.
    try:
        ts = int(now)
        hex_sign = _hmac_sha256("XgwSnGZ1p", f"ts{ts}")
        resp = await client.post(
            _TICKET_URL,
            params={
                "key_id": "ec02",
                "hexsign": hex_sign,
                "context[ts]": str(ts),
                "csrf": "",
            },
            headers=HEADERS,
        )
        ticket_data = resp.json().get("data") or {}
        expires = ts + int(ticket_data.get("ttl") or 259200)
    except Exception:
        expires = now + 60  # retry in 60s
    return cookies, expires


async def _get_anonymous_cookies() -> dict[str, str]:
    """Get or generate the full anonymous cookie set required by Bilibili.

    Returns a dict with keys: buvid3, buvid4, b_nut, b_lsid, _uuid,
    buvid_fp, bili_ticket, bili_ticket_expires.
    Caches until bili_ticket expires.
    """
    global _anonymous_cookies, _anonymous_cookies_expires, _anonymous_cookies_inflight

    now = time.time()
    if _anonymous_cookies and _anonymous_cookies_expires > now:
        return _anonymous_cookies

    async with _anonymous_cookies_lock:
        now = time.time()
        if _anonymous_cookies and _anonymous_cookies_expires > now:
            return _anonymous_cookies
        if _anonymous_cookies_inflight is None:
            _anonymous_cookies_inflight = asyncio.get_running_loop().create_future()
            fetch = True
        else:
            fetch = False
            fut = _anonymous_cookies_inflight
    if not fetch:
        return await fut
    cookies, expires = await _fetch_anonymous_cookies(now)
    async with _anonymous_cookies_lock:
        fut = _anonymous_cookies_inflight
        _anonymous_cookies_inflight = None
        _anonymous_cookies = cookies
        _anonymous_cookies_expires = expires
        if fut is not None and not fut.done():
            fut.set_result(cookies)
    return cookies


# ---------------------------------------------------------------------------
# Api class
# ---------------------------------------------------------------------------


class Api:
    """
    Represents a single Bilibili API request. Supports both the chained
    builder style (``Api(url, method, ...).update_params(**kw).result``) and
    the direct style used elsewhere (set ``.params`` then ``.request()``).

    Args match the ``bilibili_api`` ``Api(**)`` signature as consumed by the
    module ``API`` dicts (url, method, verify, wbi, credential, params, ...).
    """

    def __init__(
        self,
        url,
        method,
        comment="",
        wbi=False,
        dm=False,
        verify=False,
        no_csrf=False,
        json_body=False,
        ignore_code=False,
        sign=False,
        data=None,
        params=None,
        files=None,
        headers=None,
        credential=None,
        curl=False,
        **kwargs,
    ):
        self.url = url
        self.method = method.upper()
        self.wbi = wbi
        self.dm = dm
        self.verify = verify
        self.no_csrf = no_csrf
        self.json_body = json_body
        self.ignore_code = ignore_code
        self.sign = sign
        self.params = dict(params or {})
        self.data = dict(data or {})
        self.files = dict(files or {})
        self.headers = dict(headers or {})
        self.credential = credential if credential is not None else Credential()
        # curl=True routes through the shared Chrome-impersonating client
        # (see get_curl_client). Callers then own their full ordered header
        # set, so the player-parity header injection below is skipped.
        self.curl = curl

    def update_data(self, **kwargs) -> "Api":
        self.data = kwargs
        return self

    def update_params(self, **kwargs) -> "Api":
        self.params = kwargs
        return self

    def update_headers(self, **kwargs) -> "Api":
        self.headers = kwargs
        return self

    def _prepare_params(self):
        params = {}
        data = {}
        for k, v in self.params.items():
            if v is None:
                continue
            params[k] = int(v) if isinstance(v, bool) else v
        for k, v in self.data.items():
            if v is None:
                continue
            data[k] = int(v) if isinstance(v, bool) else v
        return params, data

    async def request(self, raw=False, byte=False):
        if self.verify:
            self.credential.raise_for_no_sessdata()
        if self.method != "GET" and not self.no_csrf:
            self.credential.raise_for_no_bili_jct()

        params, data = self._prepare_params()

        # Wbi signing (with -403 retry)
        wbi_retries = 3
        for attempt in range(wbi_retries):
            request_params = dict(params)
            if self.wbi:
                try:
                    mixin = await get_wbi_mixin_key(self.credential)
                    request_params = _enc_wbi(request_params, mixin)
                except Exception:
                    # Non-fatal: some /nav issues are tolerable
                    pass

            request_data = dict(data)
            if not self.no_csrf and self.verify and self.method in ("POST", "DELETE", "PATCH"):
                request_data["csrf"] = self.credential.bili_jct
                request_data["csrf_token"] = self.credential.bili_jct

            cookies = await _get_anonymous_cookies()
            # Override with credential cookies (only non-empty values)
            cred_cookies = self.credential.get_cookies()
            for k, v in cred_cookies.items():
                if v:
                    cookies[k] = v
            cookies["opus-goback"] = "1"

            headers = dict(HEADERS) if not self.headers else dict(self.headers)
            if not self.curl and (self.wbi or "/x/player/" in self.url):
                # Player parity: web clients attach device metadata to unified
                # requests (player_core.*.js `r0` middleware).
                headers.setdefault("x-bili-device-req-json", _DEVICE_REQ_JSON)
            json_content = None
            if self.json_body and request_data:
                headers["Content-Type"] = "application/json"
                import json as _json

                json_content = _json.dumps(request_data)
                request_data = None

            client = await get_curl_client() if self.curl else await get_bili_client()
            resp = await client.request(
                method=self.method,
                url=self.url,
                params=request_params,
                data=request_data,
                json=json_content,
                cookies=cookies,
                headers=headers,
            )

            if not byte:
                try:
                    return self._process_response(resp, raw)
                except ResponseCodeException as e:
                    if e.code == -403 and self.wbi and attempt < wbi_retries - 1:
                        recalculate_wbi()
                        continue
                    raise e
            else:
                return resp.content

        raise ResponseCodeException(-403, "WBI retry limit exceeded")

    def _process_response(self, resp, raw=False):
        if resp.status_code != 200:
            raise NetworkException(resp.status_code, resp.text)
        if resp.headers.get("content-length") == "0":
            return None
        try:
            resp_data = resp.json()
        except Exception:
            raise NetworkException(resp.status_code, "JSON parsing failed")
        if not isinstance(resp_data, dict):
            return resp_data
        if raw:
            return resp_data
        if not self.ignore_code:
            code = resp_data.get("code")
            if code is None:
                raise ResponseCodeException(-1, "API response missing code field", resp_data)
            if code != 0:
                msg = resp_data.get("msg") or resp_data.get("message") or "API returned no error message"
                raise ResponseCodeException(code, msg, resp_data)
        real_data = resp_data
        if resp_data.get("data") is not None:
            real_data = resp_data["data"]
        elif resp_data.get("result") is not None:
            real_data = resp_data["result"]
        return real_data

    @property
    async def result(self):
        return await self.request()
