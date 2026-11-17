# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
MikuInvidious local Bilibili API client.

Replacement for the archived ``bilibili-api-python`` package. Exports the
symbols used by the MikuInvidious application under the ``bilibili_api``
compatible names where practical.
"""

from . import article, audio, bangumi, comment, homepage, live, live_area, opus, search, user, video, video_zone
from .client import (
    HEADERS,
    Api,
    RequestSettings,
    close_bili_client,
    get_bili_client,
    get_bili_ticket,
    get_curl_client,
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
    "close_bili_client",
    "get_bili_client",
    "get_bili_ticket",
    "get_curl_client",
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
