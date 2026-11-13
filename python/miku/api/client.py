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
import hashlib
import hmac
import http.cookiejar
import random
import threading
import time
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

import httpx

from miku.api.wbi import _WBI_FALLBACK_TTL, OE, encode_wbi_query
from miku.api.wbi import enc_wbi as _enc_wbi
from miku.api.wbi import mixin_from_keys as _mixin_from_keys
from miku.core.fingerprints import (
    CDN_CHROME_UA,
    CURL_IMPERSONATE,
    CURL_UA,
    FIXED_CHROME_UA,
    get_web_api_headers,
)
from miku.core.fingerprints import (
    get_browser_headers as build_chrome_headers,
)
from miku.core.fingerprints import (
    get_cdn_headers as build_cdn_headers,
)
from miku.services.concurrency import Singleflight

from .credential import Credential
from .exceptions import NetworkException, ResponseCodeException

__all__ = [
    "Api",
    "close_bili_client",
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

# General Web API headers
HEADERS: dict[str, str] = get_web_api_headers()

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

# Cookies that must never be sent to Bilibili web APIs (sending bili_ticket
# triggers the anti-bot v_voucher precheck). CDN paths emit x-bili-ticket
# as an HTTP header via TicketManager.
_BLOCKED_WEB_COOKIES = frozenset({"bili_ticket", "bili_ticket_expires"})


def _has_usable_cookies(cookies: dict[str, str] | None) -> bool:
    """True when *cookies* holds at least one non-empty, non-blocked cookie."""
    if not cookies:
        return False
    return any(v for k, v in cookies.items() if v and k not in _BLOCKED_WEB_COOKIES)


def _build_cookie_header(
    *cookie_sources: dict[str, Any] | None,
    existing: str | None = None,
) -> str:
    """Build a Cookie header from *existing* and *cookie_sources*, filtering blocked cookies."""
    merged: dict[str, str] = {}
    for source in cookie_sources:
        if not source:
            continue
        for k, v in source.items():
            if not k or not v or k in _BLOCKED_WEB_COOKIES:
                continue
            merged[k] = str(v)

    parts: list[str] = []
    if existing:
        for segment in existing.split(";"):
            segment = segment.strip()
            if not segment or "=" not in segment:
                continue
            name, _, _ = segment.partition("=")
            if name.strip() in _BLOCKED_WEB_COOKIES:
                continue
            parts.append(segment)

    parts.extend(f"{k}={v}" for k, v in merged.items())
    return "; ".join(parts)


# Wbi mixin key cache
__wbi_mixin_key = ""
__wbi_mixin_key_expires = 0.0
_WBI_KEY_TTL = 43200.0  # 12 hours
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


class _NoOpCookiePolicy(http.cookiejar.CookiePolicy):
    """Disable client cookie jar storage/emission while keeping resp.cookies intact."""

    netscape = False
    rfc2965 = False
    hide_cookie2 = True

    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False

    def domain_return_ok(self, domain, request):
        return False

    def path_return_ok(self, path, request):
        return False


@dataclass
class _LoopClientEntry:
    client: httpx.AsyncClient
    proxy: str | None


# Per-event-loop registries. ALL Bilibili web-API traffic on a loop shares one
# Chrome-impersonating transport (httpx API over curl-impersonate) bound to
# that loop; keeping clients loop-scoped prevents closed/foreign loop errors in
# tests and sync() worker threads while preserving keepalive pooling.
_registry_lock = threading.Lock()
_loop_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopClientEntry] = WeakKeyDictionary()
_loop_client_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()
_loop_wbi_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()
_loop_anon_sf: WeakKeyDictionary[asyncio.AbstractEventLoop, Singleflight] = WeakKeyDictionary()


def _prune_closed_loops_locked() -> None:
    """Drop entries for closed event loops (caller must hold _registry_lock)."""
    for reg in (_loop_clients, _loop_client_locks, _loop_wbi_locks, _loop_anon_sf):
        for lp in list(reg.keys()):
            if lp.is_closed():
                reg.pop(lp, None)


def _get_loop_lock(
    registry: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock],
    loop: asyncio.AbstractEventLoop | None = None,
) -> asyncio.Lock:
    target = asyncio.get_running_loop() if loop is None else loop
    with _registry_lock:
        _prune_closed_loops_locked()
        lock = registry.get(target)
        if lock is None:
            lock = asyncio.Lock()
            registry[target] = lock
        return lock


def _get_anon_sf(loop: asyncio.AbstractEventLoop | None = None) -> Singleflight:
    target = asyncio.get_running_loop() if loop is None else loop
    with _registry_lock:
        _prune_closed_loops_locked()
        sf = _loop_anon_sf.get(target)
        if sf is None:
            sf = Singleflight()
            _loop_anon_sf[target] = sf
        return sf


def _build_curl_transport(proxy, loop: asyncio.AbstractEventLoop | None = None):
    from httpx_curl_cffi import AsyncCurlTransport

    return AsyncCurlTransport(
        impersonate=CURL_IMPERSONATE,
        default_headers=False,
        proxy=proxy,
        loop=loop,
    )


async def get_bili_client() -> httpx.AsyncClient:
    """Return the per-event-loop async client (recreated when proxy changes)."""
    loop = asyncio.get_running_loop()
    proxy = request_settings.get_proxy() or None
    with _registry_lock:
        _prune_closed_loops_locked()
        entry = _loop_clients.get(loop)
    if entry is None or entry.client.is_closed or entry.proxy != proxy:
        lock = _get_loop_lock(_loop_client_locks, loop)
        async with lock:
            proxy = request_settings.get_proxy() or None
            with _registry_lock:
                _prune_closed_loops_locked()
                entry = _loop_clients.get(loop)
            if entry is None or entry.client.is_closed or entry.proxy != proxy:
                old_client = entry.client if entry is not None and not entry.client.is_closed else None
                client = httpx.AsyncClient(
                    transport=_build_curl_transport(proxy, loop=loop),
                    trust_env=False,
                    timeout=httpx.Timeout(None, connect=15.0, pool=30.0, read=30.0),
                    follow_redirects=False,
                )
                client.cookies.jar.set_policy(_NoOpCookiePolicy())
                # httpx seeds 4 client-level defaults (accept/accept-encoding/
                # connection/user-agent) via the headers *setter* and merges
                # them ahead of per-request headers — including
                # `connection: keep-alive`, which never appears on real
                # HTTP/2 traffic and breaks our ordered Chrome set. Bypass
                # the setter so requests carry exactly what callers pass
                # (plus the transport-level pseudo-headers curl manages).
                client._headers = httpx.Headers()
                with _registry_lock:
                    _loop_clients[loop] = _LoopClientEntry(client=client, proxy=proxy)
                if old_client is not None:
                    await old_client.aclose()
                return client
    return entry.client


async def close_bili_client(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Close and unregister the client for the current running event loop."""
    running_loop = asyncio.get_running_loop()
    target_loop = running_loop if loop is None else loop
    entry: _LoopClientEntry | None = None
    with _registry_lock:
        _prune_closed_loops_locked()
        if target_loop is running_loop and not target_loop.is_closed():
            entry = _loop_clients.pop(target_loop, None)
            _loop_client_locks.pop(target_loop, None)
            _loop_wbi_locks.pop(target_loop, None)
            _loop_anon_sf.pop(target_loop, None)
    if entry is not None and not entry.client.is_closed:
        await entry.client.aclose()


async def get_curl_client() -> httpx.AsyncClient:
    """Alias: the impersonating client is now the only client (see above)."""
    return await get_bili_client()


# ---------------------------------------------------------------------------
# Wbi signing
# ---------------------------------------------------------------------------


async def _get_mixin_key(credential: Credential = None) -> str:
    credential = credential or Credential()
    client = await get_bili_client()
    headers = dict(HEADERS)
    cookies = credential.get_cookies()
    if not _has_usable_cookies(cookies):
        try:
            anon = await _get_anonymous_cookies()
            cookies = dict(anon)
        except Exception:
            pass
    cookie_str = _build_cookie_header(cookies)
    if cookie_str:
        headers["Cookie"] = cookie_str
    resp = await client.get(
        _NAV_URL,
        headers=headers,
    )
    if resp.status_code != 200:
        raise NetworkException(resp.status_code, resp.text)
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


async def get_wbi_mixin_key(credential: Credential = None) -> str:
    global __wbi_mixin_key, __wbi_mixin_key_expires
    now = time.time()
    if __wbi_mixin_key == "" or now >= __wbi_mixin_key_expires:
        wbi_lock = _get_loop_lock(_loop_wbi_locks)
        async with wbi_lock:
            now = time.time()
            if __wbi_mixin_key == "" or now >= __wbi_mixin_key_expires:
                fallback = False
                try:
                    key = await _get_mixin_key(credential)
                except Exception as exc:
                    key = ""
                    print(f"[WBI] nav failed ({exc}); using static player fallback keys")
                if len(key) != 32:
                    key = _mixin_from_keys(_FALLBACK_WBI_IMG_KEY, _FALLBACK_WBI_SUB_KEY)
                    fallback = True
                __wbi_mixin_key = key
                __wbi_mixin_key_expires = time.time() + (_WBI_FALLBACK_TTL if fallback else _WBI_KEY_TTL)
    return __wbi_mixin_key


def recalculate_wbi():
    global __wbi_mixin_key, __wbi_mixin_key_expires
    __wbi_mixin_key = ""
    __wbi_mixin_key_expires = 0.0


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
    headers = dict(HEADERS)
    cookie_str = _build_cookie_header(credential.get_cookies())
    if cookie_str:
        headers["Cookie"] = cookie_str
    resp = await client.post(
        _TICKET_URL,
        params=params,
        headers=headers,
    )
    if resp.status_code != 200:
        raise NetworkException(resp.status_code, resp.text)
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
# Anonymous cookie set (buvid3, buvid4, b_nut, b_lsid, _uuid, buvid_fp)
# ---------------------------------------------------------------------------

_anonymous_cookies: dict[str, str] = {}
_anonymous_cookies_expires: float = 0


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

    # Ensure buvid3/buvid4 are never empty strings even if /spi fails or blocks
    if not cookies.get("buvid3"):
        cookies["buvid3"] = _generate_uuid()
    if not cookies.get("buvid4"):
        cookies["buvid4"] = f"{_generate_uuid()}-infoc"

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
    """Get or generate the anonymous cookie set required by Bilibili web APIs.

    Returns a dict with keys: buvid3, buvid4, b_nut, b_lsid, _uuid, buvid_fp.
    Never includes ``bili_ticket`` (which triggers web anti-bot prechecks).
    Caches until the GenWebTicket-derived expiry timestamp.
    """
    global _anonymous_cookies, _anonymous_cookies_expires

    now = time.time()
    if _anonymous_cookies and _anonymous_cookies_expires > now:
        return _anonymous_cookies

    async def _fetch() -> dict[str, str]:
        global _anonymous_cookies, _anonymous_cookies_expires
        fetch_now = time.time()
        if _anonymous_cookies and _anonymous_cookies_expires > fetch_now:
            return _anonymous_cookies
        cookies, expires = await _fetch_anonymous_cookies(fetch_now)
        _anonymous_cookies = cookies
        _anonymous_cookies_expires = expires
        return cookies

    return await _get_anon_sf().execute("anon_cookies", _fetch)


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
        send_cookies: bool = True,
        timeout: float | httpx.Timeout | None = None,
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
        self.send_cookies = send_cookies
        self.timeout = timeout

    def update_data(self, **kwargs) -> Api:
        self.data = kwargs
        return self

    def update_params(self, **kwargs) -> Api:
        self.params = kwargs
        return self

    def update_headers(self, **kwargs) -> Api:
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
        parsed_url = httpx.URL(self.url) if self.wbi else None

        # Wbi signing (with -403 retry)
        wbi_retries = 3
        for attempt in range(wbi_retries):
            if self.wbi:
                assert parsed_url is not None
                request_params = dict(parsed_url.params)
                request_params.update(params)
                mixin = await get_wbi_mixin_key(self.credential)
                request_params = _enc_wbi(request_params, mixin)
                request_url: str | httpx.URL = parsed_url.copy_with(
                    query=encode_wbi_query(request_params).encode("ascii")
                )
                wire_params = None
            else:
                request_url = self.url
                wire_params = dict(params)

            request_data = dict(data)
            if not self.no_csrf and self.verify and self.method in ("POST", "DELETE", "PATCH"):
                request_data["csrf"] = self.credential.bili_jct
                request_data["csrf_token"] = self.credential.bili_jct

            headers = dict(HEADERS) if not self.headers else dict(self.headers)
            existing_cookie = headers.pop("Cookie", None)
            lower_cookie = headers.pop("cookie", None)
            if existing_cookie is None:
                existing_cookie = lower_cookie

            if self.send_cookies:
                anon_cookies = await _get_anonymous_cookies()
                cookie_str = _build_cookie_header(
                    anon_cookies,
                    self.credential.get_cookies(),
                    {"opus-goback": "1"},
                    existing=existing_cookie,
                )
            else:
                cookie_str = _build_cookie_header(existing=existing_cookie)

            if cookie_str:
                headers["Cookie"] = cookie_str

            if not self.curl and (self.wbi or "/x/player/" in self.url):
                # Player parity: web clients attach device metadata to unified
                # requests (player_core.*.js `r0` middleware).
                headers.setdefault("x-bili-device-req-json", _DEVICE_REQ_JSON)
            json_content = None
            if self.json_body and request_data:
                json_content = request_data
                request_data = None

            client = await get_curl_client() if self.curl else await get_bili_client()
            request_kwargs: dict[str, Any] = {
                "method": self.method,
                "url": request_url,
                "params": wire_params,
                "data": request_data,
                "json": json_content,
                "headers": headers,
            }
            if self.timeout is not None:
                request_kwargs["timeout"] = self.timeout
            resp = await client.request(**request_kwargs)

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
        except Exception as exc:
            raise NetworkException(resp.status_code, "JSON parsing failed") from exc
        if not isinstance(resp_data, dict):
            return resp_data
        if raw:
            return resp_data
        if not self.ignore_code:
            match resp_data:
                case {"code": 0}:
                    pass
                case {"code": int(code)}:
                    msg = resp_data.get("msg") or resp_data.get("message") or "API returned no error message"
                    raise ResponseCodeException(code, msg, resp_data)
                case _:
                    raise ResponseCodeException(-1, "API response missing code field", resp_data)

        match resp_data:
            case {"data": data} if data is not None:
                return data
            case {"result": result} if result is not None:
                return result
            case _:
                return resp_data

    @property
    async def result(self):
        return await self.request()
