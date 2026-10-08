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
Bilibili search module.

Drop-in replacement for ``bilibili_api.search``.  The parameter handling,
enums and the raw result dict returned by ``search_by_type`` mimic the
upstream ``bilibili-api-python`` package (v17.4.2) so that behaviour matches
exactly.  Requests are made with ``curl_cffi`` (Chrome impersonation) so
Bilibili's risk control serves full results instead of truncating them --
the same approach used by the comment module.
"""

import sys
from enum import Enum
from urllib.parse import quote, urlsplit

from .client import (
    _enc_wbi,
    _get_mixin_key,
    build_chrome_headers,
    get_curl_client,
)
from .credential import Credential
from .exceptions import ArgsException, ResponseCodeException

__all__ = [
    "SearchObjectType",
    "OrderVideo",
    "OrderLiveRoom",
    "OrderArticle",
    "OrderUser",
    "OrderCheese",
    "CategoryTypePhoto",
    "CategoryTypeArticle",
    "search",
    "search_by_type",
    "get_default_search_keyword",
    "get_hot_search_keywords",
    "get_suggest_keywords",
    "search_games",
    "search_manga",
    "search_cheese",
]


def _debug(*args):
    print("[search]", *args, file=sys.stderr, flush=True)


class SearchObjectType(Enum):
    """
    Search object.
    + VIDEO : video
    + BANGUMI : bangumi
    + FT : film & TV
    + LIVE : live
    + ARTICLE : article
    + TOPIC : topic
    + USER : user
    + LIVEUSER : live room user
    """

    VIDEO = "video"
    BANGUMI = "media_bangumi"
    FT = "media_ft"
    LIVE = "live"
    ARTICLE = "article"
    TOPIC = "topic"
    USER = "bili_user"
    LIVEUSER = "live_user"
    PHOTO = "photo"


class OrderVideo(Enum):
    """
    Video search order
    + TOTALRANK : comprehensive
    + CLICK : most clicks
    + PUBDATE : latest published
    + DM : most danmaku
    + STOW : most favorites
    + SCORES : most comments
    Ps: the order_sort field in the API determines ascending vs descending
    """

    TOTALRANK = "totalrank"
    CLICK = "click"
    PUBDATE = "pubdate"
    DM = "dm"
    STOW = "stow"
    SCORES = "scores"


class OrderLiveRoom(Enum):
    """
    Live room search order
    + NEWLIVE latest streams
    + ONLINE comprehensive
    """

    NEWLIVE = "live_time"
    ONLINE = "online"


class OrderArticle(Enum):
    """
    Article sort order
    + TOTALRANK : comprehensive
    + CLICK : most clicks
    + PUBDATE : latest published
    + ATTENTION : most likes
    + SCORES : most comments
    """

    TOTALRANK = "totalrank"
    PUBDATE = "pubdate"
    CLICK = "click"
    ATTENTION = "attention"
    SCORES = "scores"


class OrderUser(Enum):
    """
    User search sort order
    + FANS : sort by follower count
    + LEVEL : sort by level
    """

    FANS = "fans"
    LEVEL = "level"


class OrderCheese(Enum):
    """
    Course search sort order

    + RECOMMEND: comprehensive
    + SELL     : best selling
    + NEW      : newest
    + CHEEP    : lowest price
    """

    RECOMMEND = -1
    SELL = 1
    NEW = 2
    CHEEP = 3


class CategoryTypePhoto(Enum):
    """
    Photo category
    + All: all
    + DrawFriend: illustrators
    + PhotoFriend: photography
    """

    All = 0
    DrawFriend = 2
    PhotoFriend = 1


class CategoryTypeArticle(Enum):
    """
    Article category
    + All: all
    + Anime: anime
    + Game: gaming
    + TV: TV
    + Life: life
    + Hobby: hobbies
    + LightNovel: light novels
    + Technology: technology
    """

    All = 0
    Anime = 2
    Game = 1
    TV = 28
    Life = 3
    Hobby = 29
    LightNovel = 16
    Technology = 17


# curl_cffi impersonates a real browser TLS/HTTP2 fingerprint which Bilibili's
# risk control trusts (the same as the comment module uses). Search endpoints
# are wbi-signed; see .client._enc_wbi / _get_mixin_key. Headers are the full
# ordered Chrome set (see .client.build_chrome_headers); search-page calls
# carry the search origin + keyword referer, other hosts get a generic root.
_MAIN_ORIGIN = "https://www.bilibili.com"
_SEARCH_ORIGIN = "https://search.bilibili.com"


def _headers_for(url: str, params: dict) -> dict:
    host = urlsplit(url).netloc
    keyword = params.get("keyword") or params.get("term")
    if host == "api.bilibili.com" and keyword:
        return build_chrome_headers(
            origin=_SEARCH_ORIGIN,
            referer=f"{_SEARCH_ORIGIN}/all?keyword={quote(str(keyword))}",
        )
    if host.startswith("s.search.bilibili.com"):
        referer = (
            f"{_SEARCH_ORIGIN}/all?keyword={quote(str(keyword))}"
            if keyword
            else _SEARCH_ORIGIN + "/"
        )
        return build_chrome_headers(origin=_SEARCH_ORIGIN, referer=referer)
    return build_chrome_headers(origin=_MAIN_ORIGIN, referer=_MAIN_ORIGIN + "/")


async def _fetch(url: str, params: dict) -> dict:
    """Fetch JSON via an async curl_cffi session (Chrome impersonation).

    Returns the response's ``data`` or ``result`` payload (matching what
    upstream ``Api.result`` returns). Cookies are deliberately not forwarded
    so the browser impersonation isn't tripped by our generated pseudo-cookies.
    Uses the shared impersonating client (keepalive reuse).
    """
    client = await get_curl_client()
    # See live.py: keep the shared jar empty for these cookie-less calls.
    client.cookies.clear()
    resp = await client.get(
        url,
        params=params,
        headers=_headers_for(url, params),
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise ResponseCodeException(resp.status_code, f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        raise ResponseCodeException(-1, "JSON parsing failed") from None
    if not isinstance(data, dict):
        raise ResponseCodeException(-1, "API response is not a JSON object")
    if data.get("code") != 0:
        msg = data.get("msg") or data.get("message") or "API returned no error message"
        raise ResponseCodeException(data.get("code", -1), msg, data)
    if data.get("data") is not None:
        return data["data"]
    if data.get("result") is not None:
        return data["result"]
    return data


async def _wbi_get(url: str, params: dict, wbi: bool = True) -> dict:
    """GET with optional wbi signing + -403 mixin-key retry (curl_cffi).

    Mirrors the upstream ``Api(..., wbi=True).result`` retry behaviour so the
    raw result dict is returned just like ``bilibili_api.search``.  ``None``
    valued params are dropped (matching upstream ``_prepare_params``) so they
    are neither signed nor sent.
    """
    clean = {k: v for k, v in params.items() if v is not None}
    for attempt in range(3):
        request_params = dict(clean)
        try:
            if wbi:
                mixin = await _get_mixin_key()
                request_params = _enc_wbi(request_params, mixin)
            return await _fetch(url, request_params)
        except ResponseCodeException as exc:
            if exc.code == -403 and wbi and attempt < 2:
                from .client import recalculate_wbi

                recalculate_wbi()
                continue
            raise
    raise ResponseCodeException(-403, "WBI retry limit exceeded")


def _to_timestamps(time_start: str, time_end: str):
    """
    Convert ``"YYYY-MM-DD"`` strings into a pair of unix timestamps.

    Faithful port of ``bilibili_api.utils.utils.to_timestamps``.
    """
    import datetime

    start = int(datetime.datetime.strptime(time_start, "%Y-%m-%d").timestamp())
    end = int(datetime.datetime.strptime(time_end, "%Y-%m-%d").timestamp())
    return start, end


async def search(keyword: str, page: int = 1) -> dict:
    """
    Search on web with only a keyword, returning the raw dict

    Args:
        keyword (str): search keyword

        page    (int): page number. Defaults to 1.

    Returns:
        dict: raw result returned by the API
    """
    params = {"keyword": keyword, "page": page}
    return await _wbi_get("https://api.bilibili.com/x/web-interface/wbi/search/all/v2", params, wbi=True)


async def search_by_type(  # noqa: C901 - faithful port of upstream param logic
    keyword: str,
    search_type: SearchObjectType | None = None,
    order_type: OrderUser | OrderLiveRoom | OrderArticle | OrderVideo | None = None,
    time_range: int = -1,
    video_zone_type: int | None = None,
    order_sort: int | None = None,
    category_id: CategoryTypeArticle | CategoryTypePhoto | int | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    page: int = 1,
    page_size: int = 42,
) -> dict:
    """
    Search with zone, type, video duration and other filters, returning the raw dict

    Types: video, bangumi (media_bangumi), film & TV (media_ft), live, live room user (live_user),
    article, topic, user (bili_user)

    Args:
        keyword          (str): search keyword
        search_type      (SearchObjectType | None, optional): search type
        order_type       (OrderUser | OrderLiveRoom | OrderArticle | OrderVideo | None, optional): sort order  # noqa: E501
        time_range       (int, optional): duration filter in minutes, auto-mapped to a range bucket, video search only  # noqa: E501
        video_zone_type  (int | None, optional): zone filter, tid value (see video_zone module)
        order_sort       (int | None, optional): user sort direction, 0 descending (default), 1 ascending
        category_id      (CategoryTypeArticle | CategoryTypePhoto | int | None, optional): article/photo category filter  # noqa: E501
        time_start       (str, optional): start date filter, use with time_end, format: "YYYY-MM-DD"
        time_end         (str, optional): end date filter, use with time_start, format: "YYYY-MM-DD"
        page             (int, optional): page number
        page_size        (int, optional): items per page

    Returns:
        dict: raw result returned by the API
    """
    params = {"keyword": keyword, "page": page, "page_size": page_size}
    if search_type:
        params["search_type"] = search_type.value
    else:
        raise ArgsException("Missing search_type")
        # params["search_type"] = SearchObjectType.VIDEO.value
    # category_id
    if search_type.value == SearchObjectType.ARTICLE.value or search_type.value == SearchObjectType.PHOTO.value:
        if category_id:
            if isinstance(category_id, int):
                params["category_id"] = category_id
            else:
                params["category_id"] = category_id.value
    # time_code
    if search_type.value == SearchObjectType.VIDEO.value:
        if time_range > 60:
            time_code = 4
        elif 30 < time_range <= 60:
            time_code = 3
        elif 10 < time_range <= 30:
            time_code = 2
        elif 0 < time_range <= 10:
            time_code = 1
        else:
            time_code = 0
        params["duration"] = time_code
    # zone_type
    if video_zone_type:
        if isinstance(video_zone_type, int):
            params["tids"] = video_zone_type
        else:
            # Accept raw tid values or any object exposing `.value` (mirrors
            # the upstream VideoZoneTypes handling).
            params["tids"] = getattr(video_zone_type, "value", video_zone_type)
    # order_type
    if order_type:
        params["order"] = order_type.value
    # order_sort
    if search_type.value == SearchObjectType.USER.value:
        params["order_sort"] = order_sort
    # time setting
    if time_start and time_end:
        time_stamp = _to_timestamps(time_start, time_end)
        params["pubtime_begin_s"] = time_stamp[0]
        params["pubtime_end_s"] = time_stamp[1]
    return await _wbi_get("https://api.bilibili.com/x/web-interface/wbi/search/type", params, wbi=True)


async def get_default_search_keyword() -> dict:
    """
    Get the default search keyword

    Returns:
        dict: raw result returned by the API
    """
    return await _wbi_get("https://api.bilibili.com/x/web-interface/wbi/search/default", {}, wbi=True)


async def get_hot_search_keywords() -> dict:
    """
    Get hot search keywords

    Returns:
        dict: raw result returned by the API
    """
    return await _fetch("https://s.search.bilibili.com/main/hotword", {})


async def get_suggest_keywords(keyword: str) -> list[str]:
    """
    Get search suggestions for a partial keyword, like query autocompletion.

    Args:
        keyword(str): search keyword

    Returns:
        List[str]: suggested keyword list
    """
    keywords = []
    res = await _fetch("https://s.search.bilibili.com/main/suggest", {"term": keyword})
    for key in res["tag"]:
        keywords.append(key["value"])
    return keywords


async def search_games(keyword: str) -> dict:
    """
    Dedicated game search

    Args:
        keyword (str): search keyword

    Returns:
        dict: raw result returned by the API
    """
    return await _fetch("https://line1-h5-pc-api.biligame.com/game/wiki/search", {"keyword": keyword})


async def search_manga(keyword: str, page_num: int = 1, page_size: int = 9, credential: Credential = None):
    """
    Dedicated manga search

    Args:
        keyword   (str): search keyword

        page_num  (int): page number. Defaults to 1.

        page_size (int): items per page. Defaults to 9.

        credential (Credential): credential. Defaults to None.

    Returns:
        dict: raw result returned by the API
    """
    data = {"key_word": keyword, "page_num": page_num, "page_size": page_size}
    client = await get_curl_client()
    resp = await client.post(
        "https://manga.bilibili.com/twirp/comic.v1.Comic/Search?device=pc&platform=web",
        data=data,
        cookies=credential.get_cookies() if credential is not None else None,
        headers=build_chrome_headers(
            origin="https://manga.bilibili.com",
            referer="https://manga.bilibili.com/",
        ),
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise ResponseCodeException(-1, f"HTTP {resp.status_code}")
    return resp.json()


async def search_cheese(
    keyword: str,
    page_num: int = 1,
    page_size: int = 30,
    order: OrderCheese = OrderCheese.RECOMMEND,
):
    """
    Dedicated course search

    Args:
        keyword   (str)        : search keyword

        page_num  (int)        : page number. Defaults to 1.

        page_size (int)        : items per page. Defaults to 30.

        order     (OrderCheese): sort order. Defaults to OrderCheese.RECOMMEND

    Returns:
        dict: raw result returned by the API
    """
    params = {
        "word": keyword,
        "page": page_num,
        "page_size": page_size,
        "sort_type": order.value,
    }
    return await _fetch("https://api.bilibili.com/pugv/app/web/seasonSeek?classification_id=-1", params)
