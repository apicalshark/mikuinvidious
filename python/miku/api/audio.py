# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
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
