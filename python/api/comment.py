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
Bilibili comment module.

Minimal drop-in for ``bilibili_api.comment`` covering get_comments and its
enums as used by MikuInvidious.
"""

import json as _json
import sys
from enum import Enum

from curl_cffi import requests as _creq

from .client import _enc_wbi, _get_mixin_key, request_settings
from .exceptions import ArgsException, ResponseCodeException
from .video import bv2av


def _debug(*args):
    print("[comments]", *args, file=sys.stderr, flush=True)


__all__ = ["CommentResourceType", "OrderType", "get_comments", "get_sub_comments"]


class CommentResourceType(Enum):
    VIDEO = 1
    ARTICLE = 12
    DYNAMIC_DRAW = 11
    DYNAMIC = 17
    AUDIO = 14
    AUDIO_LIST = 19
    CHEESE = 33
    BLACK_ROOM = 6
    MANGA = 22
    ACTIVITY = 4


class OrderType(Enum):
    TIME = 0
    LIKE = 2


# curl_cffi impersonates a real browser TLS/HTTP2 fingerprint. Bilibili's risk
# control truncates comment responses (to ~3) for Python's default TLS stack,
# but serves full 20-item pages to a genuine browser fingerprint -- this is what
# PipePipe gets via OkHttp. We use a Chrome impersonation so comments paginate.
_IMPERSONATE = "chrome124"

# Comment requests need the pseudo-cookie set (buvid3/buvid4/...) plus a Chrome
# UA -- matching the impersonated fingerprint.
_COMMENT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "application/json, text/plain, */*",
}


async def _fetch(url: str, params: dict, cookies: dict) -> dict:
    """Fetch JSON via an async curl_cffi session (Chrome impersonation).

    NOTE: we deliberately do NOT send our generated anonymous cookies -- passing
    our fake buvid/b_nut/b_lsid set trips Bilibili's risk control and truncates
    the comment list to ~3 items, whereas the raw browser impersonation (no
    cookies, letting curl_cffi's own session/bawt handling apply) returns full
    20-item pages with working pagination.
    """
    async with _creq.AsyncSession(proxy=request_settings.get_proxy() or None) as session:
        resp = await session.get(
            url,
            params=params,
            cookies=cookies or None,
            headers=_COMMENT_HEADERS,
            impersonate=_IMPERSONATE,
            timeout=10.0,
        )
    if resp.status_code != 200:
        raise ResponseCodeException(-1, f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        raise ResponseCodeException(-1, "JSON 解析失败") from None
    if not isinstance(data, dict):
        raise ResponseCodeException(-1, "API 返回数据非 JSON 对象")
    if data.get("code") != 0:
        msg = data.get("msg") or data.get("message") or "接口未返回错误信息"
        raise ResponseCodeException(data.get("code", -1), msg, data)
    return data.get("data") or {}


async def _ensure_numeric_oid(oid):
    """Resolve a real numeric oid (aid).

    Uses PipePipe's bv2av (the current algorithm bilibili uses), which correctly
    decodes both legacy and new-format bvids into the true aid.
    """
    if isinstance(oid, str) and oid.startswith("BV"):
        return bv2av(oid)
    return int(oid)


async def get_comments(oid, type_, page_index=1, order=OrderType.TIME, credential=None, next_offset="") -> dict:
    if page_index <= 0:
        raise ArgsException("page_index 必须大于或等于 1")
    type_value = type_.value if isinstance(type_, Enum) else type_
    order_value = order.value if isinstance(order, Enum) else order
    oid_numeric = await _ensure_numeric_oid(oid)

    params = {
        "oid": oid_numeric,
        "type": type_value,
        "mode": 2 if order_value == OrderType.TIME.value else 3,
        "pagination_str": _json.dumps({"offset": next_offset}),
        "plat": 1,
        "web_location": 1315875,
    }

    # Main comment endpoint is wbi-signed. Prefer the wbi variant (matching
    # PipePipe); fall back to the plain variant only if it fails.
    data = None
    last_exc = None
    for use_wbi in (True, False):
        url = "https://api.bilibili.com/x/v2/reply/wbi/main" if use_wbi else "https://api.bilibili.com/x/v2/reply/main"
        try:
            request_params = dict(params)
            if use_wbi:
                mixin = await _get_mixin_key()
                request_params = _enc_wbi(request_params, mixin)
            data = await _fetch(url, request_params, None)
            break
        except Exception as exc:
            _debug(f"get_comments oid={oid_numeric} wbi={use_wbi} failed: {type(exc).__name__}: {exc}")
            last_exc = exc
    if data is None:
        raise last_exc or ResponseCodeException(-1, "评论接口请求失败")

    cursor = data.get("cursor") or {}

    # Merge top_replies (pinned) + replies, matching PipePipe's behavior
    top = data.get("top_replies") or []
    replies = data.get("replies") or []
    for t in top:
        t["isTop"] = True
    merged = []
    seen_rpids = set()
    for reply in top + replies:
        rpid = reply.get("rpid")
        if rpid in seen_rpids:
            continue
        seen_rpids.add(rpid)
        merged.append(reply)

    # Extract next cursor for pagination
    pagination = cursor.get("pagination_reply") or {}
    new_next_offset = pagination.get("next_offset", "")

    return {
        "page": {
            "count": cursor.get("all_count", 0),
            "num": page_index,
            "size": 20,
        },
        "replies": merged,
        "next_offset": new_next_offset,
        "is_end": cursor.get("is_end", True),
    }


async def get_sub_comments(oid, root_rpid, type_=1, page_index=1, credential=None) -> dict:
    """Fetch sub-comments (replies to a specific comment).

    Uses /x/v2/reply/reply which returns paginated sub-comments.
    """
    oid_numeric = await _ensure_numeric_oid(oid)
    params = {
        "oid": oid_numeric,
        "type": type_,
        "root": root_rpid,
        "pn": page_index,
        "ps": 20,
        "web_location": 333.788,
    }
    data = await _fetch("https://api.bilibili.com/x/v2/reply/reply", params, None)
    data = data or {}
    page_info = data.get("page") or {}
    return {
        "page": {
            "count": page_info.get("count", 0),
            "num": page_info.get("num", page_index),
            "size": page_info.get("size", 20),
        },
        "replies": data.get("replies") or [],
    }
