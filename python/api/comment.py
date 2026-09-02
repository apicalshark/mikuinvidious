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
Bilibili comment module.

Minimal drop-in for ``bilibili_api.comment`` covering get_comments and its
enums as used by MikuInvidious.
"""

from enum import Enum

from .client import Api
from .credential import Credential
from .exceptions import ArgsException

__all__ = ["CommentResourceType", "OrderType", "get_comments"]


class CommentResourceType(Enum):
    VIDEO = 1
    ARTICLE = 12
    DYNAMIC_DRAW = 11
    DYNAMIC = 17
    AUDIO = 14
    AUDIO_LIST = 19
    CHEESE = 33
    BLACK_ROOM = 6
    MANGA = 22
    ACTIVITY = 4


class OrderType(Enum):
    TIME = 0
    LIKE = 2


async def get_comments(oid, type_, page_index=1, order=OrderType.TIME, credential=None) -> dict:
    if page_index <= 0:
        raise ArgsException("page_index 必须大于或等于 1")
    type_value = type_.value if isinstance(type_, Enum) else type_
    order_value = order.value if isinstance(order, Enum) else order
    params = {"pn": page_index, "type": type_value, "oid": oid, "sort": order_value}
    credential = credential if credential is not None else Credential()
    api = {
        "url": "https://api.bilibili.com/x/v2/reply",
        "method": "GET",
        "verify": False,
    }
    return await Api(**api, credential=credential).update_params(**params).result
