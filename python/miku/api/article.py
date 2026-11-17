# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Bilibili article module.

Minimal drop-in for ``bilibili_api.article`` covering Article.get_info as
used by MikuInvidious.
"""

from .client import Api
from .credential import Credential

__all__ = ["Article"]


class Article:
    def __init__(self, cvid, credential=None):
        self.cvid = cvid
        self.credential = credential if credential is not None else Credential()

    async def get_info(self) -> dict:
        params = {"id": self.cvid}
        api = {
            "url": "https://api.bilibili.com/x/article/viewinfo",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential).update_params(**params).result
