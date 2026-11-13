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
Bilibili homepage module.

Minimal drop-in for ``bilibili_api.homepage`` covering get_videos as used
by MikuInvidious.
"""

from .client import Api
from .credential import Credential

__all__ = ["get_videos"]


async def get_videos(credential=None) -> dict:
    credential = credential if credential is not None else Credential()
    api = {
        "url": "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd",
        "method": "GET",
        "verify": False,
        "wbi": True,
    }
    return await Api(**api, credential=credential).result
