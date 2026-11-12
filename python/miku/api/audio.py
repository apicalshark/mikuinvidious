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
Bilibili audio module.

Minimal drop-in for ``bilibili_api.audio`` covering Audio (get_info,
get_download_url) and AudioList (get_info, get_song_list) as used by
MikuInvidious.
"""

from .client import Api
from .credential import Credential

__all__ = ["Audio", "AudioList"]


class Audio:
    def __init__(self, auid, credential=None):
        self.auid = auid
        self.credential = credential if credential is not None else Credential()

    async def get_info(self) -> dict:
        params = {"sid": self.auid}
        api = {
            "url": "https://www.bilibili.com/audio/music-service-c/web/song/info",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_download_url(self) -> dict:
        params = {"sid": self.auid, "privilege": 2, "quality": 2}
        api = {
            "url": "https://www.bilibili.com/audio/music-service-c/web/url",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result


class AudioList:
    def __init__(self, amid, credential=None):
        self.amid = amid
        self.credential = credential if credential is not None else Credential()

    async def get_info(self) -> dict:
        params = {"sid": self.amid}
        api = {
            "url": "https://www.bilibili.com/audio/music-service-c/web/menu/info",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_song_list(self, pn=1) -> dict:
        params = {"sid": self.amid, "pn": pn, "ps": 30}
        api = {
            "url": "https://www.bilibili.com/audio/music-service-c/web/song/of-menu",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result
