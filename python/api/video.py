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
get_aid, get_danmaku_xml and playurl resolution helpers.
"""

import base64
import json as _json
import random
import re

from .client import HEADERS, Api, get_bili_client
from .credential import Credential
from .exceptions import ArgsException

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
        raise ArgsException("bvid 提供错误，必须是以 BV 开头的纯字母和数字组成的 12 位字符串（大小写敏感）。")
    arr = list(x)
    arr[3], arr[9] = arr[9], arr[3]
    arr[4], arr[7] = arr[7], arr[4]
    sub = "".join(arr[3:])
    tmp = 0
    for c in sub:
        tmp = tmp * _BASE + _itable[c]
    return (tmp & _MASK_CODE) ^ _XOR_CODE


def av2bv(x: int) -> str:
    if x <= 0:
        raise ArgsException("aid 不能小于或等于 0。")
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


def _get_dm_img_params() -> dict[str, str]:
    """Generate dm_img fingerprint parameters required for playurl requests."""
    width = random.randint(1860, 1920)
    height = random.randint(930, 990)
    rnd = random.randint(0, 113)

    vendor, model = random.choice(_WEBGL_RENDERER_TEMPLATES)
    renderer = f"ANGLE ({vendor}, {model} Direct3D11 vs_5_0 ps_5_0, D3D11)Google Inc. ({vendor})"

    webgl_version_b64 = base64.b64encode(_WEBGL_VERSION.encode()).decode()
    renderer_b64 = base64.b64encode(renderer.encode()).decode()

    wh = [2 * width + 2 * height + 3 * rnd, 4 * width - height + rnd, rnd]
    of = [0, 0, 0]

    return {
        "dm_img_list": "[]",
        "dm_img_str": webgl_version_b64,
        "dm_cover_img_str": renderer_b64,
        "dm_img_inter": _json.dumps({"ds": [], "wh": wh, "of": of}),
    }


class Video:
    def __init__(self, bvid=None, aid=None, credential=None):
        if bvid is not None:
            self.set_bvid(bvid)
        elif aid is not None:
            self.set_aid(aid)
        else:
            raise ArgsException("请至少提供 bvid 和 aid 中的其中一个参数。")
        self.credential = credential if credential is not None else Credential()
        self._info = None

    def set_bvid(self, bvid: str) -> None:
        if not re.search(r"^BV[a-zA-Z0-9]{10}$", bvid):
            raise ArgsException("bvid 提供错误，必须是以 BV 开头的纯字母和数字组成的 12 位字符串（大小写敏感）。")
        self._bvid = bvid
        self._aid = bv2av(bvid)

    def set_aid(self, aid: int) -> None:
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            pass
        if aid <= 0:
            raise ArgsException("aid 不能小于或等于 0。")
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
                raise ArgsException("page_index 和 cid 至少提供一个。")
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
            raise ArgsException("分 p 号必须大于或等于 0。")
        info = await self._get_info_cached()
        pages = info.get("pages") or []
        if not pages:
            raise ArgsException("视频信息中不存在分 p 数据。")
        if len(pages) <= page_index:
            raise ArgsException("不存在该分 p。")
        return pages[page_index]["cid"]

    async def get_cid(self, page_index: int) -> int:
        return await self._get_cid_by_index(page_index)

    async def get_download_url(self, page_index=None, cid=None, html5=False) -> dict:
        """Fetch play URL info (returns the ``data`` node)."""
        if cid is None:
            if page_index is None:
                raise ArgsException("page_index 和 cid 至少提供一个。")
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
        return await Api(**api, credential=self.credential).update_params(**params).result

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
                raise ArgsException("page_index 和 cid 至少提供一个。")
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
        }
        return await Api(**api, credential=self.credential, wbi=True).update_params(**params).result

    async def get_danmaku_xml(self, page_index=None, cid=None) -> str:
        """Fetch raw danmaku XML (deflate-compressed, decoded to str)."""
        if cid is None:
            if page_index is None:
                raise ArgsException("page_index 和 cid 至少提供一个。")
            cid = await self._get_cid_by_index(page_index)
        client = await get_bili_client()
        resp = await client.get(
            f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}",
            headers=HEADERS,
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
        return decompressed.decode("utf-8", errors="replace")
