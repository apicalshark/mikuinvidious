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
Bilibili video module.

Minimal drop-in for ``bilibili_api.video`` covering only the methods used
by MikuInvidious: get_info, get_tags, get_related, get_pages, get_cid,
get_aid, get_danmaku_xml, get_subtitle_meta and playurl resolution helpers.
"""

import base64
import json as _json
import random
import re

from .client import HEADERS, Api, _get_anonymous_cookies, get_bili_client
from .credential import Credential
from .exceptions import ArgsException, ResponseCodeException

__all__ = ["Video"]

# bv2av / av2bv conversion (matching PipePipe's DeviceForger utils -- the
# algorithm bilibili actually uses for the current bvid format).
_XOR_CODE = 23442827791579
_MASK_CODE = 2251799813685247
_MAX_AID = 1 << 51
_BASE = 58
_table = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
_itable = {c: i for i, c in enumerate(_table)}


def bv2av(x: str) -> int:
    if (
        not isinstance(x, str)
        or not re.fullmatch(r"BV[a-zA-Z0-9]{10}", x)
        or any(char not in _itable for char in x[3:])
    ):
        raise ArgsException(
            "Invalid bvid: must be a 12-character case-sensitive string starting with BV, containing only letters and digits."
        )
    arr = list(x)
    arr[3], arr[9] = arr[9], arr[3]
    arr[4], arr[7] = arr[7], arr[4]
    sub = "".join(arr[3:])
    tmp = 0
    for c in sub:
        tmp = tmp * _BASE + _itable[c]
    return (tmp & _MASK_CODE) ^ _XOR_CODE


_bv2av_py = bv2av


def av2bv(x: int) -> str:
    if not isinstance(x, int) or x <= 0:
        raise ArgsException("aid must be greater than 0.")
    tmp = (_MAX_AID | x) ^ _XOR_CODE
    r = list("BV1000000000")
    idx = len(r) - 1
    while tmp > 0:
        r[idx] = _table[tmp % _BASE]
        tmp //= _BASE
        idx -= 1
    r[3], r[9] = r[9], r[3]
    r[4], r[7] = r[7], r[4]
    return "".join(r)


_av2bv_py = av2bv


# WebGL fingerprint data for dm_img parameters (anti-bot requirement)
_WEBGL_VERSION = "WebGL 1.0 (OpenGL ES 2.0 Chromium)"
_WEBGL_RENDERER_TEMPLATES = [
    ("Intel", "Intel(R) UHD Graphics 630"),
    ("Intel", "Intel(R) UHD Graphics 770"),
    ("Intel", "Intel(R) Iris(R) Xe Graphics"),
    ("NVIDIA", "NVIDIA GeForce RTX 3060"),
    ("NVIDIA", "NVIDIA GeForce RTX 4070"),
    ("NVIDIA", "NVIDIA GeForce GTX 1660 Ti"),
    ("AMD", "AMD Radeon RX 6700 XT"),
    ("AMD", "AMD Radeon RX 7600"),
    ("AMD", "AMD Radeon RX 580"),
]


def _get_dm_img_params(fingerprint: bool = True) -> dict[str, str]:
    """Risk-control device-log parameters for playurl requests.

    The web player (`RISK_USER_LOG` middleware in player_core.*.js) sends a
    real WebGL/canvas fingerprint via ``queryUserLog`` only when its KvSDK
    config enables it (``payload_log == "1"``); otherwise it sends
    ``dm_img_switch=0`` with no fingerprint at all. That mode works, but
    live tests (Sep 2026) show it caps anonymous quality at
    480p (tracks [16, 32]), while the WebGL-template approximation below
    returns the full anonymous ladder up to 1080p ([16, 32, 64, 80]) — so
    the faker stays the default. Pass ``fingerprint=False`` for the honest
    no-fingerprint mode.
    """
    if not fingerprint:
        return {"dm_img_switch": "0"}
    width = random.randint(1860, 1920)
    height = random.randint(930, 990)
    rnd_wh = random.randint(0, 113)

    vendor, model = random.choice(_WEBGL_RENDERER_TEMPLATES)
    renderer = f"ANGLE ({vendor}, {model} Direct3D11 vs_5_0 ps_5_0, D3D11)Google Inc. ({vendor})"

    # Bilibili's official SDK (_28b679 in bili-user-fingerprint.min.js) slices
    # the trailing 2 characters from the base64 string.
    webgl_version_b64 = base64.b64encode(_WEBGL_VERSION.encode()).decode()[:-2]
    renderer_b64 = base64.b64encode(renderer.encode()).decode()[:-2]

    # Formulas from official SDK: _186aea (wh) and _580c77 (of).
    # of on initial un-scrolled page is [rnd_of, 2*rnd_of, rnd_of];
    # static [0, 0, 0] is a detectable bot tell.
    wh = [2 * width + 2 * height + 3 * rnd_wh, 4 * width - height + rnd_wh, rnd_wh]
    rnd_of = random.randint(100, 450)
    of = [rnd_of, 2 * rnd_of, rnd_of]

    return {
        "dm_img_list": "[]",
        "dm_img_str": webgl_version_b64,
        "dm_cover_img_str": renderer_b64,
        "dm_img_inter": _json.dumps({"ds": [], "wh": wh, "of": of}, separators=(",", ":")),
    }


def is_gaia_risk_response(data) -> bool:
    """True when a playurl ``data`` node carries the Gaia risk signal.

    The web player rewrites ``code == 0`` + ``data.v_voucher`` to ``-352``
    internally and enters its captcha flow (player_core.*.js ``getGaiaRisk``);
    such responses contain no ``dash``/``durl`` node. Server-side has no
    captcha UI, so callers should retry the avoidance lane instead.
    """
    return isinstance(data, dict) and bool(data.get("v_voucher"))


class Video:
    def __init__(self, bvid=None, aid=None, credential=None):
        if bvid is not None:
            self.set_bvid(bvid)
        elif aid is not None:
            self.set_aid(aid)
        else:
            raise ArgsException("Provide at least one of bvid and aid.")
        self.credential = credential if credential is not None else Credential()
        self._info = None

    def set_bvid(self, bvid: str) -> None:
        if not re.search(r"^BV[a-zA-Z0-9]{10}$", bvid):
            raise ArgsException(
                "Invalid bvid: must be a 12-character case-sensitive string starting with BV, containing only letters and digits."
            )
        self._bvid = bvid
        self._aid = bv2av(bvid)

    def set_aid(self, aid: int) -> None:
        try:
            aid = int(aid)
        except TypeError, ValueError:
            pass
        if aid <= 0:
            raise ArgsException("aid must be greater than 0.")
        self._aid = aid
        self._bvid = av2bv(aid)

    def get_bvid(self) -> str:
        return self._bvid

    def get_aid(self) -> int:
        return self._aid

    async def get_info(self) -> dict:
        api = {
            "url": "https://api.bilibili.com/x/web-interface/wbi/view",
            "method": "GET",
            "verify": False,
            "wbi": True,
        }
        params = {"bvid": self._bvid}
        resp = await Api(**api, credential=self.credential).update_params(**params).result
        self._info = resp
        return resp

    async def _get_info_cached(self) -> dict:
        if self._info is None:
            return await self.get_info()
        return self._info

    async def get_tags(self, page_index=0, cid=None) -> list:
        if cid is None:
            if page_index is None:
                raise ArgsException("Provide at least one of page_index and cid.")
            cid = await self.get_cid(page_index=page_index)
        api = {
            "url": "https://api.bilibili.com/x/web-interface/view/detail/tag",
            "method": "GET",
            "verify": False,
        }
        params = {"bvid": self._bvid, "aid": self._aid, "cid": cid}
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_related(self) -> list:
        api = {
            "url": "https://api.bilibili.com/x/web-interface/archive/related",
            "method": "GET",
            "verify": False,
        }
        params = {"aid": self._aid, "bvid": self._bvid}
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_pages(self) -> list:
        api = {
            "url": "https://api.bilibili.com/x/player/pagelist",
            "method": "GET",
            "verify": False,
        }
        params = {"aid": self._aid, "bvid": self._bvid}
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def _get_cid_by_index(self, page_index: int) -> int:
        if page_index < 0:
            raise ArgsException("Page index must be greater than or equal to 0.")
        info = await self._get_info_cached()
        pages = info.get("pages") or []
        if not pages:
            raise ArgsException("No part data in video info.")
        if len(pages) <= page_index:
            raise ArgsException("Part does not exist.")
        return pages[page_index]["cid"]

    async def get_cid(self, page_index: int) -> int:
        return await self._get_cid_by_index(page_index)

    async def _request_playurl(self, api: dict, params: dict, label: str) -> dict:
        """GET a playurl endpoint with one Gaia-risk avoidance retry.

        On ``v_voucher`` (or ``-352``) the web player solves a captcha and
        retries with ``gaia_vtoken``; without a browser UI the supportable
        equivalent is the avoidance lane (``isGaiaAvoided=true``,
        ``gaia_source=pre-load``, ``try_look=1`` plus the WebGL-template
        ``dm_img_*`` fingerprint), which the player's own prefetch path
        uses (verified live Sep 29 2026).
        """
        try:
            data = await Api(**api, credential=self.credential).update_params(**params).result
        except ResponseCodeException as exc:
            if exc.code != -352:
                raise
            data = None
            print(f"[Video] {label} hit Gaia risk (-352) for {self._bvid}; retrying avoidance lane")
        if not is_gaia_risk_response(data):
            return data
        print(f"[Video] {label} returned v_voucher for {self._bvid}; retrying avoidance lane")
        retry = dict(params)
        retry.update(
            {
                "gaia_source": "pre-load",
                "isGaiaAvoided": "true",
                "try_look": 1,
            }
        )
        for key in ("dm_img_list", "dm_img_str", "dm_cover_img_str", "dm_img_inter"):
            retry.pop(key, None)
        retry.update(_get_dm_img_params())
        return await Api(**api, credential=self.credential).update_params(**retry).result

    async def get_download_url(self, page_index=None, cid=None, html5=False) -> dict:
        """Fetch play URL info (returns the ``data`` node)."""
        if cid is None:
            if page_index is None:
                raise ArgsException("Provide at least one of page_index and cid.")
            cid = await self._get_cid_by_index(page_index)
        params = {
            "qn": "127",
            "fnval": 4048,
            "fnver": 0,
            "fourk": 1,
            "gaia_source": "pre-load",
            "isGaiaAvoided": "true",
            "avid": self._aid,
            "bvid": self._bvid,
            "cid": cid,
            "from_client": "BROWSER",
            "web_location": 1315873,
        }
        # Add dm_img fingerprint parameters (required by Bilibili anti-bot)
        params.update(_get_dm_img_params())
        if html5:
            params["platform"] = "html5"
            params["high_quality"] = "1"
        api = {
            "url": "https://api.bilibili.com/x/player/playurl",
            "method": "GET",
            "verify": False,
        }
        return await self._request_playurl(api, params, "get_download_url")

    async def get_dash_playurl(self, page_index=None, cid=None, qn=120) -> dict:
        """Fetch DASH play URL info (returns the ``data`` node).

        Uses ``fnval=4048`` (DASH + 4K/8K + HDR + Dolby + AV1) against the
        wbi-signed playurl endpoint so Bilibili returns the ``dash`` node
        (fragmented-MP4 on-demand tracks) instead of the removed ``durl`` node.

        Returns the ``data`` dict containing ``dash`` and ``support_formats``
        (plus ``accept_quality``/``quality``) on success. Callers should treat
        an absent ``dash`` node as "DASH unavailable" (e.g. paid PGC content).
        """
        if cid is None:
            if page_index is None:
                raise ArgsException("Provide at least one of page_index and cid.")
            cid = await self._get_cid_by_index(page_index)
        params = {
            "qn": str(qn),
            "fnval": 4048,
            "fnver": 0,
            "fourk": 1,
            "gaia_source": "pre-load",
            "isGaiaAvoided": "true",
            "avid": self._aid,
            "bvid": self._bvid,
            "cid": cid,
            "from_client": "BROWSER",
            "web_location": 1315873,
            "try_look": 1,
        }
        params.update(_get_dm_img_params())
        api = {
            "url": "https://api.bilibili.com/x/player/wbi/playurl",
            "method": "GET",
            "verify": False,
            "wbi": True,
        }
        return await self._request_playurl(api, params, "get_dash_playurl")

    async def get_subtitle_meta(self, page_index=None, cid=None) -> list:
        """Fetch subtitle metadata (PipePipe ``GET_SUBTITLE_META_URL``).

        Wbi-signed ``/x/player/wbi/v2`` returns ``data.subtitle.subtitles``:
        ``[{lan, lan_doc, subtitle_url (BCC JSON), ai_status, ...}]``.
        Like PipePipe's ``ai_subtitle`` cookie gate, callers should only
        invoke this when logged in (``credential.sessdata`` set) — anonymous
        requests get an empty list. Returns [] when none exist.
        """
        if cid is None:
            if page_index is None:
                raise ArgsException("Provide at least one of page_index and cid.")
            cid = await self._get_cid_by_index(page_index)
        params = {
            "aid": self._aid,
            "cid": cid,
            "isGaiaAvoided": "false",
            "web_location": 1315873,
        }
        params.update(_get_dm_img_params())
        api = {
            "url": "https://api.bilibili.com/x/player/wbi/v2",
            "method": "GET",
            "verify": False,
            "wbi": True,
        }
        data = await self._request_playurl(api, params, "get_subtitle_meta")
        if not isinstance(data, dict):
            return []
        return (data.get("subtitle") or {}).get("subtitles") or []

    async def get_danmaku_xml(self, page_index=None, cid=None) -> str:
        """Fetch raw danmaku XML (deflate-compressed, decoded to str).

        Sends the same cookie set as every other API path (anonymous buvid
        bundle + credential override) with a per-video Referer — i.e. what a
        real browser tab sends. A cookieless request is a bot tell and makes
        upstream risk-control block pages more likely.
        """
        if cid is None:
            if page_index is None:
                raise ArgsException("Provide at least one of page_index and cid.")
            cid = await self._get_cid_by_index(page_index)
        client = await get_bili_client()
        cookies = dict(await _get_anonymous_cookies())
        for k, v in self.credential.get_cookies().items():
            if v:
                cookies[k] = v
        cookies["opus-goback"] = "1"
        try:
            from captcha_solver import get_sec_token

            sec_token = await get_sec_token()
            if sec_token:
                cookies["X-BILI-SEC-TOKEN"] = sec_token
                cookies["x-bili-gaia-vtoken"] = sec_token
        except Exception:
            pass
        headers = dict(HEADERS)
        headers["Referer"] = f"https://www.bilibili.com/video/{self._bvid}"
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)

        for attempt in range(2):
            resp = await client.get(
                f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}",
                headers=headers,
            )
            import zlib

            raw = resp.content
            try:
                decompressed = zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                try:
                    decompressed = zlib.decompress(raw)
                except Exception:
                    decompressed = raw
            text = decompressed.decode("utf-8", errors="replace")
            stripped = text.lstrip().lower()
            is_blocked = resp.status_code != 200 or stripped.startswith("<!doctype") or stripped.startswith("<html")
            if not is_blocked:
                return text

            # Check if upstream returned a Gaia challenge voucher
            if attempt == 0:
                v_voucher = resp.headers.get("x-bili-gaia-vvoucher")
                if not v_voucher:
                    import re

                    m = re.search(r"voucher_[a-zA-Z0-9\-]+", text)
                    if m:
                        v_voucher = m.group(0)
                if v_voucher:
                    try:
                        from captcha_solver import clear_sec_token, solve_and_update_sec_token

                        if sec_token:
                            await clear_sec_token()
                        new_token = await solve_and_update_sec_token(v_voucher=v_voucher, force=bool(sec_token))
                        if new_token:
                            cookies["X-BILI-SEC-TOKEN"] = new_token
                            cookies["x-bili-gaia-vtoken"] = new_token
                            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
                            continue
                    except Exception:
                        pass
            return ""
        return ""
