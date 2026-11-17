# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
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
