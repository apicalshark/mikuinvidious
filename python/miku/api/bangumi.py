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
Bilibili bangumi (anime/show) module.

Minimal drop-in for ``bilibili_api.bangumi`` covering the ``API`` dict,
``Bangumi.get_meta`` and ``Bangumi.get_episode_list`` as used by
MikuInvidious. Also mirrors ``bangumi_index_params.json`` via ``data/``.
"""

from pathlib import Path

from .client import Api
from .credential import Credential
from .exceptions import ArgsException

__all__ = ["Bangumi", "API", "get_self_media_list"]


API = {
    "info": {
        "index": {
            "url": "https://api.bilibili.com/pgc/season/index/result",
            "method": "GET",
            "verify": False,
            "params": {
                "order": "int: sort field",
                "sort": "int: sort order",
                "page": "int: page number",
                "season_type": "bangumi type",
                "pagesize": "int: items per page",
                "type": "int: unknown",
            },
        },
        "meta": {
            "url": "https://api.bilibili.com/pgc/review/user",
            "method": "GET",
            "verify": False,
            "params": {"media_id": "int: bangumi media_id (the /mdxxxx in URL)"},
        },
        "episodes_list": {
            "url": "https://api.bilibili.com/pgc/web/season/section",
            "method": "GET",
            "verify": False,
            "params": {"season_id": "int: bangumi season_id"},
        },
        "collective_info": {
            "url": "https://api.bilibili.com/pgc/view/web/simple/season",
            "method": "GET",
            "verify": False,
            "params": {"season_id": "int: unique ID for each season on Bilibili"},
        },
    }
}


def _validate_positive_id(value, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ArgsException(f"{name} must be a positive integer, not bool")
    if isinstance(value, str):
        if not value or not value.isascii() or not value.isdigit():
            raise ArgsException(f"{name} must be a positive integer")
        value = int(value)
    elif not isinstance(value, int):
        raise ArgsException(f"{name} must be a positive integer")
    if value <= 0:
        raise ArgsException(f"{name} must be greater than 0")
    return value


class Bangumi:
    def __init__(self, ssid=None, media_id=None, epid=None, credential=None, **kwargs):
        self.ssid = _validate_positive_id(ssid, "ssid")
        self.media_id = _validate_positive_id(media_id, "media_id")
        self.epid = _validate_positive_id(epid, "epid")
        if self.ssid is None and self.media_id is None and self.epid is None:
            raise ArgsException("Provide at least one of ssid, media_id, or epid")
        self.credential = credential if credential is not None else Credential()
        self.raw = None

    async def _resolve(self) -> None:
        if self.raw is not None:
            return
        api = API["info"]["collective_info"]
        if self.ssid is not None:
            params = {"season_id": self.ssid}
        elif self.epid is not None:
            params = {"ep_id": self.epid}
        elif self.media_id is not None:
            meta_resp = await (
                Api(**API["info"]["meta"], credential=self.credential).update_params(media_id=self.media_id).result
            )
            media = meta_resp.get("media") if isinstance(meta_resp, dict) else None
            season_id = media.get("season_id") if isinstance(media, dict) else None
            if not season_id:
                raise ArgsException(f"Could not resolve season_id from media_id={self.media_id}")
            self.ssid = _validate_positive_id(season_id, "ssid")
            params = {"season_id": self.ssid}
        else:
            raise ArgsException("Provide at least one of ssid, media_id, or epid")

        resp = await Api(**api, credential=self.credential).update_params(**params).result
        if not isinstance(resp, dict):
            resp = {}
        self.raw = resp
        if self.media_id is None and resp.get("media_id"):
            self.media_id = resp["media_id"]
        if self.ssid is None and resp.get("season_id"):
            self.ssid = resp["season_id"]

    def get_season_id(self) -> int:
        return self.ssid

    async def get_media_id(self) -> int:
        if self.media_id is None:
            await self._resolve()
        return self.media_id

    async def get_meta(self) -> dict:
        api = API["info"]["meta"]
        params = {"media_id": await self.get_media_id()}
        return await Api(**api, credential=self.credential).update_params(**params).result

    async def get_episode_list(self) -> dict:
        if not self.raw or self.ssid is None:
            await self._resolve()
        api = API["info"]["episodes_list"]
        params = {"season_id": self.ssid}
        return await Api(**api, credential=self.credential).update_params(**params).result


_data_dir = Path(__file__).parent / "data"


async def get_self_media_list(pn=1, ps=24, credential=None):
    """Return the user's bangumi follow list (not fully implemented in this module, placeholder)."""
    return {"list": [], "pn": pn, "ps": ps}
