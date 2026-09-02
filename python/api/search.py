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
Bilibili search module.

Minimal drop-in for ``bilibili_api.search`` covering search_by_type and
the enums used by MikuInvidious (SearchObjectType, OrderVideo, OrderArticle,
OrderUser).
"""

from enum import Enum

from .client import Api
from .exceptions import ArgsException

__all__ = [
    "SearchObjectType",
    "OrderVideo",
    "OrderArticle",
    "OrderUser",
    "search_by_type",
]


class SearchObjectType(Enum):
    VIDEO = "video"
    LIVE = "live"
    ARTICLE = "article"
    USER = "bili_user"
    LIVEUSER = "live_user"


class OrderVideo(Enum):
    TOTALRANK = "totalrank"
    CLICK = "click"
    PUBDATE = "pubdate"
    DM = "dm"
    STOW = "stow"
    SCORES = "scores"


class OrderArticle(Enum):
    ATTENTION = "attention"
    SCORES = "scores"


class OrderUser(Enum):
    ATTENTION = "attention"
    FANS = "fans"
    LEVEL = "level"


async def search_by_type(
    keyword,
    page=1,
    page_size=42,
    search_type=None,
    order_type=None,
    time_range=-1,
    order_sort=None,
    retries: int = 3,
) -> dict:
    import asyncio as _asyncio

    params = {"keyword": keyword, "page": page, "page_size": page_size}
    if search_type is None:
        raise ArgsException("缺少 search_type")
    params["search_type"] = search_type.value if isinstance(search_type, SearchObjectType) else search_type

    if isinstance(search_type, SearchObjectType) and search_type == SearchObjectType.VIDEO:
        params["duration"] = _to_time_code(time_range)
    if order_type is not None:
        params["order"] = order_type.value if isinstance(order_type, Enum) else order_type
    if isinstance(search_type, SearchObjectType) and search_type == SearchObjectType.USER:
        params["order_sort"] = order_sort
    api = {
        "url": "https://api.bilibili.com/x/web-interface/wbi/search/type",
        "method": "GET",
        "verify": False,
    }
    last_result = {}
    for attempt in range(max(retries, 1)):
        last_result = await Api(**api, wbi=True).update_params(**params).result
        if not isinstance(last_result, dict):
            last_result = {}
        num = last_result.get("numResults", 0)
        result = last_result.get("result", [])
        if num > 0 or (isinstance(result, list) and len(result) > 0) or (isinstance(result, dict) and result):
            return last_result
        if attempt < retries - 1:
            await _asyncio.sleep(0.5 * (attempt + 1))
    return last_result


def _to_time_code(time_range):
    if time_range > 60:
        return 4
    elif 30 < time_range <= 60:
        return 3
    elif 10 < time_range <= 30:
        return 2
    elif 0 < time_range <= 10:
        return 1
    return 0
