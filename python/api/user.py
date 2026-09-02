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
Bilibili user module.

Minimal drop-in for ``bilibili_api.user`` covering get_user_info,
get_videos and get_articles as used by MikuInvidious.
"""

from enum import Enum

from .client import Api
from .credential import Credential
from .exceptions import ArgsException

__all__ = ["User", "VideoOrder", "ArticleOrder"]


class VideoOrder(Enum):
    """投稿排序方式。"""
    PUBDATE = "pubdate"
    CLICK = "click"
    STOW = "stow"


class ArticleOrder(Enum):
    """专栏排序方式。"""
    PUBDATE = "publish_time"
    FAVORITE = "favorite"
    VIEW = "view"


class User:
    def __init__(self, uid=None, name=None, credential=None):
        if uid is not None:
            if uid <= 0:
                raise ArgsException("uid 不能小于或等于 0")
            self.uid = uid
        elif name is not None:
            self.name = name
            self.uid = None
        else:
            raise ArgsException("uid 和 name 必须提供一个")
        self.credential = credential if credential is not None else Credential()

    async def get_user_info(self) -> dict:
        params = {"mid": self.uid}
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/acc/info",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential, wbi=True).update_params(**params).result

    async def get_videos(self, tid=0, pn=1, ps=30, keyword="", order=VideoOrder.PUBDATE) -> dict:
        if isinstance(order, VideoOrder):
            order = order.value
        params = {
            "mid": self.uid,
            "ps": ps,
            "tid": tid,
            "pn": pn,
            "keyword": keyword,
            "order": order,
            "order_avoided": True,
            "platform": "web",
            "web_location": 1550101,
        }
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/arc/search",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential, wbi=True).update_params(**params).result

    async def get_articles(self, pn=1, order=ArticleOrder.PUBDATE, ps=30) -> dict:
        if isinstance(order, ArticleOrder):
            order = order.value
        params = {"mid": self.uid, "ps": ps, "pn": pn, "sort": order}
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/article",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
