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

from .client import (
    Api,
    build_chrome_headers,
)
from .exceptions import ArgsException, ResponseCodeException
from .video import bv2av


def _debug(*args):
    print("[comments]", *args, file=sys.stderr, flush=True)


__all__ = ["CommentResourceType", "OrderType", "get_comments", "get_sub_comments"]

# Terminal reply-zone code: the uploader closed the comment section.
# Retrying unsigned cannot clear it (and currently just eats a -352), so the
# wbi attempt re-raises immediately instead of falling through to plain.
COMMENTS_CLOSED_CODE = 12061


def _is_terminal_comment_error(exc: Exception) -> bool:
    """True when no retry (e.g. the unsigned fallback) can clear the error."""
    return isinstance(exc, ResponseCodeException) and exc.code == COMMENTS_CLOSED_CODE


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
# Headers are the full ordered Chrome set (see .client.build_chrome_headers);
# UA/client-hints match the impersonation target (chrome152) by construction.
_MAIN_ORIGIN = "https://www.bilibili.com"


async def _ensure_numeric_oid(oid) -> int:
    """Resolve a real numeric oid (aid).

    Uses PipePipe's bv2av (the current algorithm bilibili uses), which correctly
    decodes both legacy and new-format bvids into the true aid.
    """
    if isinstance(oid, bool):
        raise ArgsException("Invalid oid: bool is not allowed")
    if isinstance(oid, str):
        if oid.startswith("BV"):
            return bv2av(oid)
        raw = oid[2:] if oid.startswith(("av", "AV")) else oid
        if not raw or not raw.isascii() or not raw.isdigit():
            raise ArgsException("Invalid oid: must be a valid BV id or positive integer")
        oid_int = int(raw)
        if oid_int <= 0:
            raise ArgsException("oid must be greater than 0")
        return oid_int
    if isinstance(oid, int):
        if oid <= 0:
            raise ArgsException("oid must be greater than 0")
        return oid
    raise ArgsException("Invalid oid: must be a valid BV id or positive integer")


def _validate_page_index(page_index) -> int:
    if isinstance(page_index, bool) or not isinstance(page_index, int) or page_index <= 0:
        raise ArgsException("page_index must be greater than or equal to 1")
    return page_index


async def get_comments(oid, type_, page_index=1, order=OrderType.TIME, credential=None, next_offset="") -> dict:
    page_index = _validate_page_index(page_index)
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
    # PipePipe); fall back to the plain variant only if it fails — except for
    # terminal codes like a closed comment section, which no retry can clear.
    # Cookies are deliberately omitted (send_cookies=False) so generated
    # device cookies don't trigger ~3-item risk-control truncation.
    headers = build_chrome_headers(origin=_MAIN_ORIGIN, referer=_MAIN_ORIGIN + "/")
    data = None
    last_exc = None
    for use_wbi in (True, False):
        url = "https://api.bilibili.com/x/v2/reply/wbi/main" if use_wbi else "https://api.bilibili.com/x/v2/reply/main"
        try:
            data = await (
                Api(
                    url=url,
                    method="GET",
                    verify=False,
                    wbi=use_wbi,
                    curl=True,
                    send_cookies=False,
                    timeout=10.0,
                    headers=headers,
                    credential=credential,
                )
                .update_params(**params)
                .result
            )
            break
        except Exception as exc:
            _debug(f"get_comments oid={oid_numeric} wbi={use_wbi} failed: {type(exc).__name__}: {exc}")
            if _is_terminal_comment_error(exc):
                raise
            last_exc = exc
    if data is None:
        raise last_exc or ResponseCodeException(-1, "Comment API request failed")

    data = data if isinstance(data, dict) else {}

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
    page_index = _validate_page_index(page_index)
    oid_numeric = await _ensure_numeric_oid(oid)
    params = {
        "oid": oid_numeric,
        "type": type_,
        "root": root_rpid,
        "pn": page_index,
        "ps": 20,
        "web_location": 333.788,
    }
    headers = build_chrome_headers(origin=_MAIN_ORIGIN, referer=_MAIN_ORIGIN + "/")
    data = await (
        Api(
            url="https://api.bilibili.com/x/v2/reply/reply",
            method="GET",
            verify=False,
            wbi=False,
            curl=True,
            send_cookies=False,
            timeout=10.0,
            headers=headers,
            credential=credential,
        )
        .update_params(**params)
        .result
    )
    data = data if isinstance(data, dict) else {}
    page_info = data.get("page") or {}
    return {
        "page": {
            "count": page_info.get("count", 0),
            "num": page_info.get("num", page_index),
            "size": page_info.get("size", 20),
        },
        "replies": data.get("replies") or [],
    }
