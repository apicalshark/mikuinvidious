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
