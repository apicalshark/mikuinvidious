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
MikuInvidious local Bilibili API client.

Replacement for the archived ``bilibili-api-python`` package. Exports the
symbols used by the MikuInvidious application under the ``bilibili_api``
compatible names where practical.
"""

from . import article, audio, comment, homepage, live, live_area, opus, search, user, video, video_zone
from . import bangumi
from .client import (
    Api,
    HEADERS,
    RequestSettings,
    get_bili_client,
    get_bili_ticket,
    get_wbi_mixin_key,
    refresh_bili_ticket,
    request_settings,
)
from .credential import Credential, sync
from .exceptions import ApiException, ArgsException, CookiesRefreshException, NetworkException, ResponseCodeException

__all__ = [
    "article",
    "audio",
    "comment",
    "homepage",
    "live",
    "live_area",
    "opus",
    "search",
    "user",
    "video",
    "video_zone",
    "bangumi",
    "Api",
    "HEADERS",
    "RequestSettings",
    "get_bili_client",
    "get_bili_ticket",
    "get_wbi_mixin_key",
    "refresh_bili_ticket",
    "request_settings",
    "Credential",
    "sync",
    "ApiException",
    "ArgsException",
    "CookiesRefreshException",
    "NetworkException",
    "ResponseCodeException",
]
