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
Bilibili video zone module.

Minimal drop-in for ``bilibili_api.video_zone`` covering
get_zone_new_videos as used by MikuInvidious.
"""

from .client import Api

__all__ = ["get_zone_new_videos"]


async def get_zone_new_videos(tid, page_num=1, page_size=10) -> dict:
    params = {"rid": tid, "pn": page_num, "ps": page_size}
    api = {
        "url": "https://api.bilibili.com/x/web-interface/dynamic/region",
        "method": "GET",
        "verify": False,
    }
    return await Api(**api).update_params(**params).result
