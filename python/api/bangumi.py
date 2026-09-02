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
Bilibili bangumi (anime/show) module.

Minimal drop-in for ``bilibili_api.bangumi`` covering the ``API`` dict,
``Bangumi.get_meta`` and ``Bangumi.get_episode_list`` as used by
MikuInvidious. Also mirrors ``bangumi_index_params.json`` via ``data/``.
"""

import os

from .client import Api
from .credential import Credential

__all__ = ["Bangumi", "API", "get_self_media_list"]


API = {
    "info": {
        "index": {
            "url": "https://api.bilibili.com/pgc/season/index/result",
            "method": "GET",
            "verify": False,
            "params": {
                "order": "int: 排序字段",
                "sort": "int: 排序方式",
                "page": "int: 页数",
                "season_type": "番剧类型",
                "pagesize": "int: 每页数量",
                "type": "int: unknown",
            },
        },
        "meta": {
            "url": "https://api.bilibili.com/pgc/review/user",
            "method": "GET",
            "verify": False,
            "params": {"media_id": "int: 番剧的 media_id(URL 中的/mdxxxx)"},
        },
        "episodes_list": {
            "url": "https://api.bilibili.com/pgc/web/season/section",
            "method": "GET",
            "verify": False,
            "params": {"season_id": "int: 番剧的 season_id"},
        },
        "collective_info": {
            "url": "https://api.bilibili.com/pgc/view/web/simple/season",
            "method": "GET",
            "verify": False,
            "params": {"season_id": "int: B 站每个剧集会对应一个唯一 ID"},
        },
    }
}


class Bangumi:
    def __init__(self, ssid=None, media_id=None, epid=None, credential=None, **kwargs):
        self.ssid = ssid
        self.media_id = media_id
        self.epid = epid
        self.credential = credential if credential is not None else Credential()
        self.raw = None

    async def _resolve(self) -> None:
        if self.raw is not None:
            return
        api = API["info"]["collective_info"]
        params = {"season_id": self.ssid}
        resp = await Api(**api, credential=self.credential).update_params(**params).result
        self.raw = resp
        if self.media_id is None and "media_id" in resp:
            self.media_id = resp["media_id"]
        if self.ssid is None and "season_id" in resp:
            self.ssid = resp["season_id"]

    def get_season_id(self) -> int:
        return self.ssid

    async def get_media_id(self) -> int:
        if self.media_id is None:
            await self._resolve()
        return self.media_id

    async def get_meta(self) -> dict:
        api = API["info"]["meta"]
        params = {"media_id": await self.get_media_id()}
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_episode_list(self) -> dict:
        if not self.raw:
            await self._resolve()
        api = API["info"]["episodes_list"]
        params = {"season_id": self.ssid}
        return await Api(**api, credential=self.credential).update_params(**params).result


_data_dir = os.path.join(os.path.dirname(__file__), "data")


async def get_self_media_list(pn=1, ps=24, credential=None):
    """返回用户追番列表（本模块未完整实现，预留）。"""
    return {"list": [], "pn": pn, "ps": ps}
