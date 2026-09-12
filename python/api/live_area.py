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
Bilibili live area module.

Minimal drop-in for ``bilibili_api.live_area`` covering get_list_by_area as
used by MikuInvidious.
"""

from .client import Api
from .credential import Credential

__all__ = ["get_list_by_area"]

# Parent id mapping for common live areas (id -> parent_area_id). When the id
# is itself a main area it maps to itself; unknown ids fall back to themselves.
_PARENT_AREA = {
    1: 1,
    2: 2,
    3: 3,
    6: 6,
    9: 3,
    13: 3,
    7: 3,
    8: 3,
    10: 3,
    11: 3,
    12: 3,
    14: 3,
    15: 3,
    16: 3,
    17: 3,
    19: 3,
    20: 3,
    28: 1,
    33: 2,
    35: 2,
    38: 2,
    39: 2,
    92: 3,
    145: 3,
    178: 3,
    223: 1,
    224: 1,
    228: 3,
    229: 3,
    240: 6,
    262: 6,
    280: 6,
    282: 2,
    283: 2,
    332: 2,
    366: 3,
    369: 3,
    372: 3,
    374: 3,
    375: 3,
    378: 3,
    381: 3,
    518: 3,
}


async def get_list_by_area(area_id, page=1, order="", credential=None) -> dict:
    credential = credential if credential is not None else Credential()
    parent_area_id = _PARENT_AREA.get(area_id, area_id)
    area_id_param = area_id if area_id in _PARENT_AREA else 0
    params = {
        "platform": "web",
        "parent_area_id": parent_area_id,
        "area_id": area_id_param,
        "page": page,
        "sort_type": order,
        "web_location": "444.253",
    }
    api = {
        "url": "https://api.live.bilibili.com/xlive/web-interface/v1/second/getList",
        "method": "GET",
        "verify": False,
    }
    return await Api(**api, credential=credential, wbi=True).update_params(**params).result
