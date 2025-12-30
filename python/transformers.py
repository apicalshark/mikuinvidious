# transformers.py


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
            "title": data.get("title", "").replace('<em class="keyword">', "").replace("</em>", ""),
            "pic": data.get("pic", "") or data.get("cover", ""),
            "duration": format_duration(data.get("duration") or data.get("length")),
            "author": data.get("owner", {}).get("name") or data.get("author") or data.get("upname", "Unknown"),
            "author_id": data.get("owner", {}).get("mid") or data.get("mid") or data.get("upmid", 0),
            "views": data.get("stat", {}).get("view") or data.get("play") or 0,
            "danmaku": data.get("stat", {}).get("danmaku") or data.get("video_review") or 0,
            "published": data.get("pubdate") or data.get("created") or 0,
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
    desc = vinfo.get("desc", "")
    desc = proxy_html_images(desc)
    return {
        "bvid": vinfo.get("bvid"),
        "title": vinfo.get("title"),
        "desc": desc,
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
        title = data.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
        pic = data.get("cover") or data.get("user_cover") or data.get("system_cover")
        uname = data.get("uname") or data.get("name")
        face = data.get("face") or data.get("uface")
        uid = data.get("uid") or data.get("mid")
        online = data.get("online") or data.get("watched_show", {}).get("num") or 0
        area_name = data.get("area_name") or data.get("cate_name")

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


def proxy_html_images(html_content):
    """Replaces all external image URLs in HTML with proxied versions."""
    import re

    if not html_content:
        return html_content

    # Match http/https or protocol-relative URLs in src attributes
    # We exclude URLs that already start with /static/ or /proxy/
    def replace_src(match):
        prefix = match.group(1)
        url = match.group(2)
        suffix = match.group(3)
        
        if url.startswith("static/") or url.startswith("proxy/"):
            return match.group(0)
            
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith("http"):
            url = "https://" + url
            
        return f'{prefix}/proxy/pic/{url}{suffix}'

    pattern = r'(src=["\'])(?:https?:)?//((?!static/|proxy/)[^"\']+\.[^"\']+)(["\'])'
    return re.sub(pattern, replace_src, html_content)


def transform_live_room(data):
    """Standardizes detailed live room info."""
    import re

    # This depends on the specific API return structure of get_room_info
    room_info = data.get("room_info", {})
    anchor_info = data.get("anchor_info", {})
    base_info = anchor_info.get("base_info", {})

    raw_desc = room_info.get("description", "")
    # Split into paragraphs by double newlines
    paragraphs = re.split(r"\n\s*\n", raw_desc)
    formatted_paragraphs = []
    for p in paragraphs:
        if p.strip():
            # Replace single newlines within a paragraph with <br>
            inner = p.strip().replace("\n", "<br>")
            formatted_paragraphs.append(f"<p>{inner}</p>")

    formatted_desc = "".join(formatted_paragraphs)
    formatted_desc = proxy_html_images(formatted_desc)

    return {
        "room_id": room_info.get("room_id"),
        "title": room_info.get("title"),
        "pic": room_info.get("cover"),
        "online": room_info.get("online"),
        "description": formatted_desc,
        "area_name": room_info.get("area_name"),
        "parent_area_name": room_info.get("parent_area_name"),
        "live_status": room_info.get("live_status"),  # 1: Live, 0: Offline
        "start_time": room_info.get("live_start_time"),
        "uname": base_info.get("uname"),
        "face": base_info.get("face"),
        "uid": room_info.get("uid"),
    }
