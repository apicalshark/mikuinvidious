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
Bilibili opus module.

Minimal drop-in for ``bilibili_api.opus`` covering Opus.get_info as used
by MikuInvidious.
"""

from .client import Api
from .credential import Credential
from .exceptions import ArgsException

__all__ = ["Opus"]


class Opus:
    def __init__(self, cvid, credential=None):
        self.id = cvid
        self.credential = credential if credential is not None else Credential()

    async def get_info(self) -> dict:
        params = {
            "timezone_offset": -480,
            "id": self.id,
            "features": "onlyfansVote,onlyfansAssetsV2,decorationCard,htmlNewStyle,ugcDelete,editable,opusPrivateVisible",
        }
        api = {
            "url": "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail",
            "method": "GET",
            "verify": False,
        }
        info = await Api(**api, credential=self.credential).update_params(**params).result
        if info.get("fallback"):
            raise ArgsException("传入的 opus_id 不正确")
        return info
