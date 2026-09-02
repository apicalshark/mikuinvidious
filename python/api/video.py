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

import re

from .client import Api, get_bili_client, HEADERS
from .credential import Credential
from .exceptions import ArgsException

__all__ = ["Video"]

# bv2av / av2bv conversion (adapted from bilibili-API-collect)
_table = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
_itable = {c: i for i, c in enumerate(_table)}
_s = [11, 10, 3, 8, 4, 6]
_XOR = 177451812
_ADD = 8728348608


def bv2av(x: str) -> int:
    if not re.match(r"^BV[a-zA-Z0-9]{10}$", x):
        raise ArgsException("bvid 提供错误，必须是以 BV 开头的纯字母和数字组成的 12 位字符串（大小写敏感）。")
    r = 0
    for i in range(6):
        r += _itable[x[_s[i]]] * 58 ** i
    return (r - _ADD) ^ _XOR


def av2bv(x: int) -> str:
    if x <= 0:
        raise ArgsException("aid 不能小于或等于 0。")
    x = (x ^ _XOR) + _ADD
    r = list("BV1  4 1 7  ")
    for i in range(6):
        r[_s[i]] = _table[x // 58 ** i % 58]
    return "".join(r)


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
            raise ArgsException(
                "bvid 提供错误，必须是以 BV 开头的纯字母和数字组成的 12 位字符串（大小写敏感）。"
            )
        self._bvid = bvid
        self._aid = bv2av(bvid)

    def set_aid(self, aid: int) -> None:
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
            "url": "https://api.bilibili.com/x/web-interface/view",
            "method": "GET",
            "verify": False,
        }
        params = {"bvid": self._bvid, "aid": self._aid}
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
        if html5:
            params["platform"] = "html5"
            params["high_quality"] = "1"
        api = {
            "url": "https://api.bilibili.com/x/player/wbi/playurl",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential, wbi=True).update_params(**params).result

    async def get_danmaku_xml(self, page_index=None, cid=None) -> str:
        """Fetch raw danmaku XML (bytes decoded to str)."""
        if cid is None:
            if page_index is None:
                raise ArgsException("page_index 和 cid 至少提供一个。")
            cid = await self._get_cid_by_index(page_index)
        client = await get_bili_client()
        resp = await client.get(
            f"https://comment.bilibili.com/{cid}.xml",
            headers=HEADERS,
        )
        return resp.content.decode("utf-8")
