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

from curl_cffi import requests as _creq

from .client import _enc_wbi, _get_mixin_key, request_settings
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
    搜索对象。
    + VIDEO : 视频
    + BANGUMI : 番剧
    + FT : 影视
    + LIVE : 直播
    + ARTICLE : 专栏
    + TOPIC : 话题
    + USER : 用户
    + LIVEUSER : 直播间用户
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
    视频搜索类型
    + TOTALRANK : 综合排序
    + CLICK : 最多点击
    + PUBDATE : 最新发布
    + DM : 最多弹幕
    + STOW : 最多收藏
    + SCORES : 最多评论
    Ps: Api 中 的 order_sort 字段决定顺序还是倒序
    """

    TOTALRANK = "totalrank"
    CLICK = "click"
    PUBDATE = "pubdate"
    DM = "dm"
    STOW = "stow"
    SCORES = "scores"


class OrderLiveRoom(Enum):
    """
    直播间搜索类型
    + NEWLIVE 最新开播
    + ONLINE 综合排序
    """

    NEWLIVE = "live_time"
    ONLINE = "online"


class OrderArticle(Enum):
    """
    文章的排序类型
    + TOTALRANK : 综合排序
    + CLICK : 最多点击
    + PUBDATE : 最新发布
    + ATTENTION : 最多喜欢
    + SCORES : 最多评论
    """

    TOTALRANK = "totalrank"
    PUBDATE = "pubdate"
    CLICK = "click"
    ATTENTION = "attention"
    SCORES = "scores"


class OrderUser(Enum):
    """
    搜索用户的排序类型
    + FANS : 按照粉丝数量排序
    + LEVEL : 按照等级排序
    """

    FANS = "fans"
    LEVEL = "level"


class OrderCheese(Enum):
    """
    课程搜索排序类型

    + RECOMMEND: 综合
    + SELL     : 销量最高
    + NEW      : 最新上架
    + CHEEP    : 售价最低
    """

    RECOMMEND = -1
    SELL = 1
    NEW = 2
    CHEEP = 3


class CategoryTypePhoto(Enum):
    """
    相册分类
    + All 全部
    + DrawFriend 画友
    + PhotoFriend 摄影
    """

    All = 0
    DrawFriend = 2
    PhotoFriend = 1


class CategoryTypeArticle(Enum):
    """
    文章分类
    + All 全部
    + Anime 动画
    + Game 游戏
    + TV 电视
    + Life 生活
    + Hobby 兴趣
    + LightNovel 轻小说
    + Technology 科技
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
# are wbi-signed; see .client._enc_wbi / _get_mixin_key.
_IMPERSONATE = "chrome124"

_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "application/json, text/plain, */*",
}


async def _fetch(url: str, params: dict) -> dict:
    """Fetch JSON via an async curl_cffi session (Chrome impersonation).

    Returns the response's ``data`` or ``result`` payload (matching what
    upstream ``Api.result`` returns). Cookies are deliberately not forwarded
    so the browser impersonation isn't tripped by our generated pseudo-cookies.
    """
    async with _creq.AsyncSession(proxy=request_settings.get_proxy() or None) as session:
        resp = await session.get(
            url,
            params=params,
            cookies=None,
            headers=_SEARCH_HEADERS,
            impersonate=_IMPERSONATE,
            timeout=10.0,
        )
    if resp.status_code != 200:
        raise ResponseCodeException(resp.status_code, f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        raise ResponseCodeException(-1, "JSON 解析失败") from None
    if not isinstance(data, dict):
        raise ResponseCodeException(-1, "API 返回数据非 JSON 对象")
    if data.get("code") != 0:
        msg = data.get("msg") or data.get("message") or "接口未返回错误信息"
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
    raise ResponseCodeException(-403, "Wbi 重试次数超过限制")


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
    只指定关键字在 web 进行搜索，返回未经处理的字典

    Args:
        keyword (str): 搜索关键词

        page    (int): 页码. Defaults to 1.

    Returns:
        dict: 调用 API 返回的结果
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
    指定分区，类型，视频长度等参数进行搜索，返回未经处理的字典

    类型：视频(video)、番剧(media_bangumi)、影视(media_ft)、直播(live)、直播用户(liveuser)、
    专栏(article)、话题(topic)、用户(bili_user)

    Args:
        keyword          (str): 搜索关键词
        search_type      (SearchObjectType | None, optional): 搜索类型
        order_type       (OrderUser | OrderLiveRoom | OrderArticle | OrderVideo | None, optional): 排序  # noqa: E501
        time_range       (int, optional): 指定时间，自动转换到指定区间，只在视频类型下生效  # noqa: E501
        video_zone_type  (int | None, optional): 话题类型，指定 tid (可使用 video_zone 模块查询)
        order_sort       (int | None, optional): 用户粉丝数/等级排序 默认为0 由高到低：0 由低到高：1
        category_id      (CategoryTypeArticle | CategoryTypePhoto | int | None, optional): 专栏/相簿筛选  # noqa: E501
        time_start       (str, optional): 指定开始时间，与结束时间搭配使用，格式为："YYYY-MM-DD"
        time_end         (str, optional): 指定结束时间，与开始时间搭配使用，格式为："YYYY-MM-DD"
        page             (int, optional): 页码
        page_size        (int, optional): 每一页的数据大小

    Returns:
        dict: 调用 API 返回的结果
    """
    params = {"keyword": keyword, "page": page, "page_size": page_size}
    if search_type:
        params["search_type"] = search_type.value
    else:
        raise ArgsException("缺少 search_type")
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
    获取默认的搜索内容

    Returns:
        dict: 调用 API 返回的结果
    """
    return await _wbi_get("https://api.bilibili.com/x/web-interface/wbi/search/default", {}, wbi=True)


async def get_hot_search_keywords() -> dict:
    """
    获取热搜

    Returns:
        dict: 调用 API 返回的结果
    """
    return await _fetch("https://s.search.bilibili.com/main/hotword", {})


async def get_suggest_keywords(keyword: str) -> list[str]:
    """
    通过一些文字输入获取搜索建议。类似搜索词的联想。

    Args:
        keyword(str): 搜索关键词

    Returns:
        List[str]: 关键词列表
    """
    keywords = []
    res = await _fetch("https://s.search.bilibili.com/main/suggest", {"term": keyword})
    for key in res["tag"]:
        keywords.append(key["value"])
    return keywords


async def search_games(keyword: str) -> dict:
    """
    搜索游戏特用函数

    Args:
        keyword (str): 搜索关键词

    Returns:
        dict: 调用 API 返回的结果
    """
    return await _fetch("https://line1-h5-pc-api.biligame.com/game/wiki/search", {"keyword": keyword})


async def search_manga(keyword: str, page_num: int = 1, page_size: int = 9, credential: Credential = None):
    """
    搜索漫画特用函数

    Args:
        keyword   (str): 搜索关键词

        page_num  (int): 页码. Defaults to 1.

        page_size (int): 每一页的数据大小. Defaults to 9.

        credential (Credential): 凭据类. Defaults to None.

    Returns:
        dict: 调用 API 返回的结果
    """
    data = {"key_word": keyword, "page_num": page_num, "page_size": page_size}
    async with _creq.AsyncSession(proxy=request_settings.get_proxy() or None) as session:
        resp = await session.post(
            "https://manga.bilibili.com/twirp/comic.v1.Comic/Search?device=pc&platform=web",
            data=data,
            cookies=credential.get_cookies() if credential is not None else None,
            headers=_SEARCH_HEADERS,
            impersonate=_IMPERSONATE,
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
    搜索课程特用函数

    Args:
        keyword   (str)        : 搜索关键词

        page_num  (int)        : 页码. Defaults to 1.

        page_size (int)        : 每一页的数据大小. Defaults to 30.

        order     (OrderCheese): 排序方式. Defaults to OrderCheese.RECOMMEND

    Returns:
        dict: 调用 API 返回的结果
    """
    params = {
        "word": keyword,
        "page": page_num,
        "page_size": page_size,
        "sort_type": order.value,
    }
    return await _fetch("https://api.bilibili.com/pugv/app/web/seasonSeek?classification_id=-1", params)
