# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
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
            raise ArgsException("Invalid opus_id provided")
        return info
