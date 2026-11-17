# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
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
