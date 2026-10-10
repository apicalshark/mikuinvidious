# transformers.py

import html
import re
from urllib.parse import urlparse


def format_duration(seconds):
    if not seconds:
        return "00:00"
    if isinstance(seconds, str) and ":" in seconds:
        return seconds
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
    except Exception:
        return "00:00"


def strip_search_highlight(text):
    """Remove Bilibili search keyword highlight tags.

    The search API wraps matched keywords in ``<em class="keyword">…</em>``,
    with quote/attribute variants seen in the wild (single quotes, extra
    attrs). Exact-string replaces miss those and the raw tag leaks into
    cards (e.g. live search for 原神 showing ``<em class="keyword">原神</em>``).
    Strip any ``<em …>`` / ``</em>`` robustly; non-strings pass through as "".
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""
    return re.sub(r"</?em[^>]*>", "", text)


def format_description(raw_desc):
    """Escapes HTML and formats newlines into paragraphs/breaks."""
    if not raw_desc:
        return ""
    # Split into paragraphs by double newlines
    paragraphs = re.split(r"\n\s*\n", raw_desc)
    formatted_paragraphs = []
    for p in paragraphs:
        if p.strip():
            # Escape HTML content to prevent XSS, then format newlines
            safe_text = html.escape(p.strip())
            # Replace single newlines within a paragraph with <br>
            inner = safe_text.replace("\n", "<br>")
            formatted_paragraphs.append(f"<p>{inner}</p>")

    return "".join(formatted_paragraphs)


def transform_video_card(data):
    """Standardizes video objects for grid displays."""
    try:
        # Search API uses 'bvid', timeline uses 'bvid', some use 'aid'
        bvid = data.get("bvid") or data.get("id")
        if not bvid and "aid" in data:
            bvid = f"av{data['aid']}"
        if not bvid:
            return None

        return {
            "bvid": bvid,
            "title": strip_search_highlight(data.get("title", "")),
            "pic": data.get("pic", "") or data.get("cover", ""),
            "duration": format_duration(data.get("duration") or data.get("length")),
            "author": strip_search_highlight(data.get("owner", {}).get("name") or data.get("author") or data.get("upname", "Unknown")),
            "author_id": data.get("owner", {}).get("mid") or data.get("mid") or data.get("upmid", 0),
            "views": data.get("stat", {}).get("view") or data.get("play") or 0,
            "danmaku": data.get("stat", {}).get("danmaku") or data.get("video_review") or 0,
            "published": data.get("pubdate") or data.get("created") or 0,
            # Category label (mirrors live cards' area_name) + raw tag list
            # from the video search API (`typename` e.g. 手机游戏, `tag` is a
            # comma-separated string e.g. "原神,二次元,...").
            "typename": strip_search_highlight(data.get("typename") or data.get("tname") or ""),
            "tags": [
                t
                for t in (
                    strip_search_highlight(x.strip())
                    for x in str(data.get("tag") or "").split(",")
                )
                if t
            ],
        }
    except Exception:
        return None


def transform_user_info(uinfo):
    """Standardizes user profile data."""
    return {
        "mid": uinfo.get("mid"),
        "name": uinfo.get("name"),
        "face": uinfo.get("face"),
        "sign": uinfo.get("sign"),
        "level": uinfo.get("level"),
        "fans": uinfo.get("follower") or 0,
        "following": uinfo.get("following") or 0,
    }


def transform_video_detail(vinfo):
    """Standardizes detailed video metadata."""
    stat = vinfo.get("stat", {})
    return {
        "bvid": vinfo.get("bvid"),
        "title": vinfo.get("title"),
        "desc": vinfo.get("desc"),
        "pic": vinfo.get("pic"),
        "pubdate": vinfo.get("pubdate"),
        "author": vinfo.get("owner", {}).get("name"),
        "author_id": vinfo.get("owner", {}).get("mid"),
        "author_face": vinfo.get("owner", {}).get("face"),
        "views": stat.get("view", 0),
        "likes": stat.get("like", 0),
        "coins": stat.get("coin", 0),
        "favorites": stat.get("favorite", 0),
        "shares": stat.get("share", 0),
        "danmaku_count": stat.get("danmaku", 0),
    }


def transform_live_card(data):
    """Standardizes live room cards."""
    try:
        # Normalize keys
        room_id = data.get("roomid") or data.get("room_id")
        title = strip_search_highlight(data.get("title", ""))
        pic = data.get("cover") or data.get("user_cover") or data.get("system_cover")
        uname = strip_search_highlight(data.get("uname") or data.get("name") or "")
        face = data.get("face") or data.get("uface")
        uid = data.get("uid") or data.get("mid")
        online = data.get("online") or data.get("watched_show", {}).get("num") or 0
        area_name = strip_search_highlight(data.get("area_name") or data.get("cate_name") or "")

        return {
            "bvid": room_id,  # Compatibility with home.html
            "room_id": room_id,
            "title": title,
            "pic": pic,
            "uname": uname,
            "author": uname,  # Compatibility with home.html
            "author_id": uid,  # Compatibility with home.html
            "online": online,
            "views": online,  # Compatibility with home.html
            "area_name": area_name,
            "face": face,
            "uid": uid,
            "duration": "LIVE",
            "published": 0,
        }
    except Exception:
        return None


def transform_live_room(data):
    """Standardizes detailed live room info."""
    # Supports both getInfoByRoom (room_info/anchor_info) and the
    # getRoomBaseInfo fallback (flat by_room_ids entry — PipePipe 1e330b87).
    if not isinstance(data, dict):
        data = {}
    room_info = data.get("room_info", {}) or {}
    anchor_info = data.get("anchor_info", {}) or {}
    base_info = anchor_info.get("base_info", {}) or {}
    if not room_info and ("room_id" in data or "title" in data):
        room_info = data

    raw_desc = room_info.get("description", "") or ""

    face = base_info.get("face") or data.get("face") or room_info.get("face") or ""
    # live_start_time / live_time may be None (offline room), 0, or a
    # numeric string — normalize to int/None so the `date` filter never 500s.
    _raw_start = room_info.get("live_start_time") or room_info.get("live_time")
    try:
        start_time = int(_raw_start) if _raw_start else None
    except (ValueError, TypeError):
        start_time = None
    pic = (
        room_info.get("cover")
        or room_info.get("cover_from_user")
        or data.get("cover")
        or ""
    )
    if isinstance(pic, str) and pic.startswith("http:"):
        pic = "https:" + pic[4:]
    if isinstance(face, str) and face.startswith("http:"):
        face = "https:" + face[4:]

    return {
        "room_id": room_info.get("room_id"),
        "title": room_info.get("title"),
        "pic": pic,
        "online": room_info.get("online"),
        "description": raw_desc,
        "area_name": room_info.get("area_name"),
        "parent_area_name": room_info.get("parent_area_name"),
        "live_status": room_info.get("live_status"),  # 1: Live, 0: Offline
        "start_time": start_time,
        "uname": base_info.get("uname") or room_info.get("uname"),
        "face": face,
        "uid": room_info.get("uid"),
    }


def transform_article_card(data):
    """Standardizes article objects for grid displays."""
    try:
        title = strip_search_highlight(data.get("title", ""))
        return {
            "id": data.get("id"),
            "title": title,
            "image_urls": data.get("image_urls", []),
            "mid": data.get("mid"),
            "author": data.get("author"),
            "pub_time": data.get("pub_time"),
            "view": data.get("view", 0),
        }
    except Exception:
        return None


def transform_user_card(data):
    """Standardizes user search results."""
    try:
        uname = strip_search_highlight(data.get("uname", ""))
        return {
            "mid": data.get("mid"),
            "uname": uname,
            "upic": data.get("upic"),
            "usign": data.get("usign"),
            "level": data.get("level"),
            "fans": data.get("fans", 0),
            "videos": data.get("videos", 0),
        }
    except Exception:
        return None


# Reply emoji (Bilibili comment emotes) — safe server-side renderer.
#
# Bilibili's web client (seed/jinkela/commentpc/bili-comments.js) tokenizes
# content.message on [...] placeholders and replaces exact matches from
# content.emote with <img src=webp_url||gif_url||url>. All image hosts are
# *.hdslb.com, so the existing /proxy/pic/ route proxies them with zero
# client contact to Bilibili (and same-origin satisfies CSP img-src 'self').
#
# Privacy/safety rules (no tracking or injection from upstream data):
# - message text is HTML-escaped first; only our own <img> tags are emitted.
# - upstream URLs are re-validated against the proxy domain allowlist (mirror
#   of proxy.is_safe_proxy_url, syntactic only — no DNS here) and rewritten
#   to same-origin /proxy/pic/<host><path>; query/fragment are DROPPED so
#   tracking params never reach the CDN fetch nor the client.
# - size-2 sticker data-* attrs (emoji-jump-url etc.) are deliberately NOT
#   rendered: our frontend has no consumer, and jump_url is arbitrary upstream.
# - styles are fixed constants; alt text is escaped; no inline JS anywhere.
_REPLY_EMOTE_DOMAINS = (
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
)

_REPLY_EMOTE_SIZE1_STYLE = "width:1.4em;height:1.4em;vertical-align:text-bottom;"
_REPLY_EMOTE_SIZE2_STYLE = "width:50px;height:50px;"

# Linear, bracket-balanced token scan (mirrors the [...] tokenizer upstream).
_REPLY_EMOTE_TOKEN_RE = re.compile(r"\[[^\[\]\n\r]{1,50}\]")


def _proxied_emote_src(raw_url):
    """Validate an upstream emote URL; same-origin /proxy/pic/ path or None."""
    if not raw_url or not isinstance(raw_url, str):
        return None
    url = raw_url.strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host or not any(host == d.lstrip(".") or host.endswith(d) for d in _REPLY_EMOTE_DOMAINS):
        return None
    path = parts.path or "/"
    # Reject anything that could break out of the src attribute or path.
    if re.search(r"[\s\"'<>\\^`{|}]", path):
        return None
    if ".." in path.split("/"):
        return None
    return "/proxy/pic/" + host + path


def render_reply_content(content):
    """Render a comment content dict to safe HTML with proxied emoji.

    Returns an escaped-text string with [...] emote placeholders replaced by
    same-origin <img> tags. Unknown/blocked placeholders stay as plain text.
    """
    if isinstance(content, str):
        return html.escape(content)
    if not isinstance(content, dict):
        return ""
    message = content.get("message") or ""
    if not isinstance(message, str):
        message = str(message)
    emote = content.get("emote")
    if not isinstance(emote, dict) or not emote:
        return html.escape(message)

    def _replace(match):
        token = match.group(0)
        entry = emote.get(token)
        if not isinstance(entry, dict):
            return html.escape(token)
        raw = entry.get("webp_url") or entry.get("gif_url") or entry.get("url")
        src = _proxied_emote_src(raw)
        if not src:
            return html.escape(token)
        try:
            size = int((entry.get("meta") or {}).get("size", 1))
        except (TypeError, ValueError):
            size = 1
        style = _REPLY_EMOTE_SIZE2_STYLE if size == 2 else _REPLY_EMOTE_SIZE1_STYLE
        alt = html.escape(token, quote=True)
        return f'<img src="{src}" alt="{alt}" loading="lazy" style="{style}">'

    return _REPLY_EMOTE_TOKEN_RE.sub(_replace, html.escape(message))
