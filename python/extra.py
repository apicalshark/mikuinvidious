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

"""Bilibili extra apis"""

import asyncio
import orjson
import re

from bilibili_api.exceptions import ArgsException
from bilibili_api.utils.network import Api
from bs4 import BeautifulSoup
from shared import Network, appredis


def get_article_info(article_text, cid):
    """Extract article info from INITIAL_STATE in HTML."""
    pattern = re.compile(r"window\.__INITIAL_STATE__\s*=\s*({.*?});", re.DOTALL)
    match = pattern.search(article_text)

    arinfo = {
        "title": "",
        "author": {"mid": 0, "face": "", "name": ""},
        "publish_time": 0,
        "stats": {"view": 0, "like": 0, "coin": 0, "favorite": 0, "share": 0},
    }

    if match:
        state = orjson.loads(match.group(1))
        detail = state.get("detail", {})

        # Try to find modules
        modules = detail.get("modules", [])
        if not modules and "item" in detail:
            modules = detail["item"].get("modules", [])

        for module in modules:
            m_type = module.get("module_type")

            # Title
            if m_type == "MODULE_TYPE_TITLE" or module.get("module_title"):
                title_obj = module.get("module_title", {})
                arinfo["title"] = title_obj.get("text", "")

            # Author
            elif m_type == "MODULE_TYPE_AUTHOR" or module.get("module_author"):
                author = module.get("module_author", {})
                arinfo["author"]["mid"] = author.get("mid")
                arinfo["author"]["name"] = author.get("name")
                arinfo["author"]["face"] = author.get("face")
                arinfo["publish_time"] = author.get("pub_ts", 0)

            # Stats
            elif m_type == "MODULE_TYPE_STAT" or module.get("module_stat"):
                stat = module.get("module_stat", {})
                arinfo["stats"]["like"] = stat.get("like", {}).get("count", 0)
                arinfo["stats"]["coin"] = stat.get("coin", {}).get("count", 0)
                arinfo["stats"]["favorite"] = stat.get("favorite", {}).get("count", 0)
                arinfo["stats"]["share"] = stat.get("forward", {}).get("count", 0)
                if "view" in stat:
                    arinfo["stats"]["view"] = stat.get("view", {}).get("count", 0)

        if not arinfo["title"] and "basic" in detail:
            arinfo["title"] = detail["basic"].get("title", "").replace(" - 哔哩哔哩", "")

        if not arinfo["author"]["mid"] and "basic" in detail:
            arinfo["author"]["mid"] = detail["basic"].get("uid", 0)

        if not arinfo["stats"]["view"] and "basic" in detail:
            arinfo["stats"]["view"] = detail["basic"].get("view_count", 0)

    return arinfo


"""Format the result returned by cv link."""


def article_to_html(article_text):
    article_soup = BeautifulSoup(article_text, features="lxml")
    article_body = article_soup.find("div", id="read-article-holder")

    if not article_body:
        # Try to parse from INITIAL_STATE if it's a new format article
        pattern = re.compile(r"window\.__INITIAL_STATE__\s*=\s*({.*?});", re.DOTALL)
        match = pattern.search(article_text)
        if match:
            try:
                state = orjson.loads(match.group(1))
                detail = state.get("detail", {})
                modules = detail.get("modules", [])
                if not modules and "item" in detail:
                    modules = detail["item"].get("modules", [])

                content_html = ""
                for module in modules:
                    if module.get("module_type") == "MODULE_TYPE_CONTENT" or module.get("module_content"):
                        paragraphs = module.get("module_content", {}).get("paragraphs", [])
                        for p in paragraphs:
                            p_type = p.get("para_type")
                            if p_type == 1:  # Text
                                text_nodes = p.get("text", {}).get("nodes", [])
                                p_content = ""
                                for node in text_nodes:
                                    text = ""
                                    url = None
                                    is_bold = False
                                    is_italic = False
                                    color = None

                                    if node.get("rich"):
                                        rich = node["rich"]
                                        text = rich.get("text", "")
                                        url = rich.get("jump_url")
                                        if rich.get("emoji"):
                                            emoji_url = rich["emoji"].get("icon_url")
                                            if emoji_url:
                                                if emoji_url.startswith("//"):
                                                    emoji_url = "https:" + emoji_url
                                                proxied_emoji = "/proxy/pic/" + emoji_url.split("//")[1]
                                                emoji_style = (
                                                    "width: 1.2em; height: 1.2em; display: inline-block; "
                                                    "vertical-align: middle;"
                                                )
                                                text = f'<img src="{proxied_emoji}" style="{emoji_style}" alt="{text}">'
                                    elif node.get("word"):
                                        word = node["word"]
                                        text = word.get("words", "")
                                        if word.get("style"):
                                            is_bold = word["style"].get("bold")
                                            is_italic = word["style"].get("italic")
                                            color = word["style"].get("color")

                                    # Fallback for older format if necessary
                                    if not text and node.get("type") == "TEXT_NODE_TYPE_WORD":
                                        word = node.get("word", {})
                                        text = word.get("words", "")
                                        is_bold = word.get("bold")
                                        is_italic = word.get("italic")
                                        color = word.get("color")
                                    elif not text and node.get("type") == "TEXT_NODE_TYPE_HYPERLINK":
                                        word = node.get("word", {})
                                        text = word.get("words", "")
                                        url = node.get("link", {}).get("url")

                                    if is_bold:
                                        text = f"<strong>{text}</strong>"
                                    if is_italic:
                                        text = f"<em>{text}</em>"
                                    if color:
                                        if not color.startswith("#"):
                                            color = f"#{color}"
                                        text = f'<span style="color: {color};">{text}</span>'

                                    if url:
                                        text = f'<a href="{url}">{text}</a>'

                                    p_content += text

                                # Alignment
                                align_style = ""
                                align = p.get("align")
                                if align == 2 or align == 1:  # Center (opus uses 1 for center sometimes?)
                                    align_style = ' style="text-align: center;"'
                                elif align == 3:  # Right
                                    align_style = ' style="text-align: right;"'

                                content_html += f"<p{align_style}>{p_content}</p>"
                            elif p_type == 2:  # Image
                                pics = p.get("pic", {}).get("pics", [])
                                for pic in pics:
                                    img_url = pic.get("url")
                                    if img_url:
                                        proxied_url = "/proxy/pic/" + img_url.split("//")[1]
                                        img_html = f'<img src="{proxied_url}" class="mx-auto">'
                                        content_html += f'<figure style="text-align: center;">{img_html}</figure>'
                            elif p_type == 7:  # Code
                                code = p.get("code", {})
                                lang = code.get("lang", "").replace("language-", "")
                                content = code.get("content", "")
                                # Use html.escape if available, or just a simple replacement
                                import html as html_lib

                                content = html_lib.escape(content)
                                content_html += f'<pre><code class="language-{lang}">{content}</code></pre>'

                if content_html:
                    return f'<div id="main-article">{content_html}</div>'
            except Exception as e:
                print(f"Error parsing opus INITIAL_STATE: {e}")
                pass
        return "<p>无法解析文章内容。</p>"

    article_body.attrs = {}
    article_body["id"] = "main-article"

    for child in article_body.find_all(True):  # find_all(True) gets all tags
        # Handle headers
        if child.name.startswith("h") and len(child.name) == 2:
            try:
                level = int(child.name[1:])
                child.name = f"h{min(level + 1, 6)}"
            except ValueError:
                pass

        # Handle images
        if child.name == "img":
            if child.has_attr("data-src"):
                child["src"] = "/proxy/pic/" + child["data-src"].split("//")[1]
            elif child.has_attr("src") and child["src"].startswith("//"):
                child["src"] = "/proxy/pic/" + child["src"].split("//")[1]

            # Remove all other attributes except src and add mx-auto class
            src = child.get("src", "")
            child.attrs = {"src": src, "class": "mx-auto"}
            continue

        # Handle links
        if child.name == "a":
            if child.has_attr("href"):
                href = child["href"]
                if "bilibili.com" in href:
                    # Try to make it relative if it's a bilibili link
                    href = href.split("bilibili.com")[-1]
                child["href"] = href
            # Keep only href
            href = child.get("href", "#")
            child.attrs = {"href": href}
            continue

        # Preserve some styles like alignment and color
        new_style = []
        if child.has_attr("style"):
            style = child["style"]
            # Preserve text-align
            match_align = re.search(r"text-align\s*:\s*([^;]+)", style)
            if match_align:
                new_style.append(f"text-align: {match_align.group(1).strip()}")

            # Preserve color
            match_color = re.search(r"color\s*:\s*([^;]+)", style)
            if match_color:
                new_style.append(f"color: {match_color.group(1).strip()}")

            # Preserve font-weight
            match_weight = re.search(r"font-weight\s*:\s*([^;]+)", style)
            if match_weight:
                new_style.append(f"font-weight: {match_weight.group(1).strip()}")

        new_attrs = {}
        if new_style:
            new_attrs["style"] = "; ".join(new_style) + ";"

        # Keep classes for some elements if they look useful, but mostly clear them
        # Bilibili uses a lot of specific classes for layout.

        child.attrs = new_attrs

    return str(article_body)


"""Convert the article to any file."""


async def article_to_any(article_text, dest_fmt):
    cmd = ["pandoc", "-f", "html", "-t", dest_fmt, "-"]
    p = None
    try:
        p = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await p.communicate(input=article_to_html(article_text).encode("utf-8"))
        if p.returncode != 0:
            print(f"[Pandoc] Error converting article: {stderr.decode('utf-8')}")
        return stdout.decode("utf-8")
    except Exception as e:
        print(f"[Pandoc] Exception in article_to_any: {e}")
        return ""
    finally:
        if p and p.returncode is None:
            try:
                p.terminate()
                await p.wait()
            except Exception:
                pass


async def video_get_src_for_qn(vi, idx, quality=16, ep_id=None):
    """Get a specific available source for video."""
    cid = await vi.get_cid(idx)
    api = Api(
        "https://api.bilibili.com/x/player/playurl",
        "GET",
        verify=(not not vi.credential.sessdata),
        credential=vi.credential,
    )
    api.params = {"avid": vi.get_aid(), "cid": cid, "qn": quality, "platform": "html5", "high_quality": 1}

    # Try standard UGC API first
    res = {}
    try:
        res = await api.request()
    except Exception as e:
        # Check if it's a -404 error (ResponseCodeException usually has .code)
        if hasattr(e, "code") and e.code == -404:
            print("[Extra] PGC Fallback for SRC (caught exception)")
            try:
                client = await Network.get_async_client()
                cookies = {}
                if vi.credential and vi.credential.sessdata:
                    cookies = {
                        "SESSDATA": vi.credential.sessdata,
                        "bili_jct": vi.credential.bili_jct,
                        "buvid3": vi.credential.buvid3,
                        "buvid4": vi.credential.buvid4,
                        "DedeUserID": vi.credential.dedeuserid,
                    }

                pgc_params = api.params.copy()

                # [Fix] Extract ep_id from redirect_url for PGC, unless provided
                if not ep_id:
                    try:
                        info = await vi.get_info()
                        redirect_url = info.get("redirect_url", "")
                        if redirect_url:
                            ep_match = re.search(r"ep(\d+)", redirect_url)
                            if ep_match:
                                ep_id = ep_match.group(1)
                    except Exception:
                        pass

                if ep_id:
                    pgc_params["ep_id"] = ep_id
                pgc_res_raw = await client.get(
                    "https://api.bilibili.com/pgc/player/web/playurl",
                    params=pgc_params,
                    cookies=cookies,
                    headers={
                        "Referer": "https://www.bilibili.com",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    },
                    follow_redirects=True,
                )
                pgc_res = pgc_res_raw.json()
                if pgc_res and pgc_res.get("code") == 0:
                    res_node = pgc_res.get("result")
                    if isinstance(res_node, dict):
                        return res_node
                    return pgc_res
            except Exception as pgc_e:
                print(f"[Extra] PGC Fallback SRC failed: {pgc_e}")
        # Re-raise original exception if fallback not taken or failed
        # If we successfully handled it, we would have returned.
        # But wait, providing a way to return raw error in 'res' variable for existing logic is harder with exception.
        # Existing logic expected 'res' dict. 'video_get_src_for_qn' usually returns dict.
        # If we re-raise, the caller views.py might handle it.
        # BUT, looking at original code: 'return res' at the end.
        pass

    # Fallback to PGC if UGC -404 (in case it didn't raise but returned error dict, though likely it raised)
    if res.get("code") == -404:
        # ... logic for non-raising client ...
        pass  # Already handled in except block ideally, but kept for safety if api client changes behavior

    # Clean standard UGC return
    if "data" in res:
        return res["data"]
    # If no data and no PGC fallback success, simply return res (it's likely the error dict or empty)
    return res


async def _pgc_dash_playurl(vi, idx, ep_id=None):
    """Bangumi/PGC playurl fallback when UGC WBI playurl is unavailable."""
    cid = await vi.get_cid(idx)
    client = await Network.get_async_client()
    cookies = {}
    if vi.credential and vi.credential.sessdata:
        cookies = {
            "SESSDATA": vi.credential.sessdata,
            "bili_jct": vi.credential.bili_jct,
            "buvid3": vi.credential.buvid3,
            "buvid4": vi.credential.buvid4,
            "DedeUserID": vi.credential.dedeuserid,
        }

    if not ep_id:
        try:
            info = await vi.get_info()
            redirect_url = info.get("redirect_url", "")
            if redirect_url:
                ep_match = re.search(r"ep(\d+)", redirect_url)
                if ep_match:
                    ep_id = ep_match.group(1)
        except Exception:
            pass

    pgc_params = {
        "avid": vi.get_aid(),
        "cid": cid,
        "fnval": 4048,
        "fnver": 0,
        "fourk": 1,
        "qn": 127,
    }
    if ep_id:
        pgc_params["ep_id"] = ep_id

    pgc_res_raw = await client.get(
        "https://api.bilibili.com/pgc/player/web/playurl",
        params=pgc_params,
        cookies=cookies,
        headers={
            "Referer": "https://www.bilibili.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        },
        follow_redirects=True,
    )
    pgc_res = pgc_res_raw.json()
    if pgc_res and pgc_res.get("code") == 0:
        res_node = pgc_res.get("result")
        if isinstance(res_node, dict) and "dash" in res_node:
            return res_node
    return None


def _max_dash_video_qn(data):
    videos = (data or {}).get("dash", {}).get("video", [])
    if not videos:
        return 0
    return max(int(v.get("id", 0)) for v in videos)


async def _wbi_playurl(vi, idx, qn=127, fnval=4048, platform=None):
    """WBI-signed playurl with explicit qn/fnval."""
    cid = await vi.get_cid(idx)
    api = Api(
        "https://api.bilibili.com/x/player/wbi/playurl",
        "GET",
        credential=vi.credential,
        wbi=True,
    )
    params = {
        "qn": int(qn),
        "fnval": int(fnval),
        "fnver": 0,
        "fourk": 1,
        "gaia_source": "pre-load",
        "isGaiaAvoided": "true",
        "avid": vi.get_aid(),
        "bvid": vi.get_bvid(),
        "cid": cid,
        "from_client": "BROWSER",
        "web_location": 1315873,
    }
    if platform:
        params["platform"] = platform
        if platform == "html5":
            params["high_quality"] = 1
    return await api.update_params(**params).result


def _merge_dash_data(base, extra):
    """Merge supplemental playurl into base DASH (higher qn tracks)."""
    if not base or "dash" not in base or not extra or "dash" not in extra:
        return base

    def track_key(track):
        return (int(track.get("id", 0)), int(track.get("codecid", 0)))

    video_by_key = {track_key(v): v for v in base["dash"].get("video", [])}
    for v in extra["dash"].get("video", []):
        k = track_key(v)
        if k not in video_by_key or int(v.get("id", 0)) > int(video_by_key[k].get("id", 0)):
            video_by_key[k] = v
    base["dash"]["video"] = sorted(video_by_key.values(), key=lambda v: int(v.get("id", 0)))

    audio_by_key = {track_key(a): a for a in base["dash"].get("audio", [])}
    for a in extra["dash"].get("audio", []):
        k = track_key(a)
        if k not in audio_by_key:
            audio_by_key[k] = a
    base["dash"]["audio"] = list(audio_by_key.values())

    sf = {int(f.get("quality", 0)): f for f in base.get("support_formats", [])}
    for f in extra.get("support_formats", []):
        q = int(f.get("quality", 0))
        if q not in sf:
            sf[q] = f
    base["support_formats"] = sorted(sf.values(), key=lambda f: int(f.get("quality", 0)), reverse=True)
    return base


async def _supplement_dash_qualities(vi, idx, data):
    """Request higher qn/fnval playurls when the bulk DASH response omits 720p+ tracks."""
    if not data.get("dash", {}).get("video"):
        return data

    combos = [
        (64, 16),
        (80, 16),
        (64, 4048),
        (80, 4048),
        (116, 16),
        (127, 16),
    ]
    for qn, fnval in combos:
        if _max_dash_video_qn(data) >= qn:
            continue
        try:
            extra = await _wbi_playurl(vi, idx, qn=qn, fnval=fnval)
            if not isinstance(extra, dict) or not extra.get("dash"):
                continue
            ids = sorted({int(v.get("id", 0)) for v in extra["dash"].get("video", [])})
            print(f"[Extra] DASH try qn={qn} fnval={fnval} -> video ids {ids}")
            before = _max_dash_video_qn(data)
            data = _merge_dash_data(data, extra)
            after = _max_dash_video_qn(data)
            if after > before:
                print(f"[Extra] DASH supplemented qn={qn} fnval={fnval}: max {before} -> {after}")
        except Exception as e:
            print(f"[Extra] DASH try qn={qn} fnval={fnval} failed: {e}")
    return data


def _playurl_granted_qn(data):
    if not data:
        return 0
    return int(data.get("quality", 0))


def _has_durl(data):
    return bool(data and data.get("durl"))


async def fetch_mp4_playurls_parallel(vi, idx, ep_id=None):
    """
    Run alongside DASH fetch: same credential, comparable WBI playurl + legacy html5 at qn=64.
    Picks the best durl response (highest granted qn).
    """
    probes = [
        ("WBI fnval=1 qn=64 (MP4)", lambda: _wbi_playurl(vi, idx, qn=64, fnval=1)),
        ("WBI fnval=16 qn=64 (DASH)", lambda: _wbi_playurl(vi, idx, qn=64, fnval=16)),
        ("WBI fnval=4048 qn=64", lambda: _wbi_playurl(vi, idx, qn=64, fnval=4048)),
        ("legacy html5 playurl qn=64", lambda: video_get_src_for_qn(vi, idx, 64, ep_id=ep_id)),
    ]

    results = await asyncio.gather(*(fn() for _, fn in probes), return_exceptions=True)

    best = None
    best_qn = 0
    for (label, _), res in zip(probes, results):
        if isinstance(res, Exception):
            print(f"[Video] Parallel MP4 {label} error: {res}")
            continue
        if not isinstance(res, dict):
            continue
        granted = _playurl_granted_qn(res)
        vmax = _max_dash_video_qn(res) if res.get("dash") else 0
        print(
            f"[Video] Parallel MP4 {label}: granted={granted} "
            f"durl={_has_durl(res)} dash_max_id={vmax or '-'}"
        )
        if _has_durl(res) and granted >= best_qn:
            best_qn = granted
            best = res
    return best


def _progressive_url_payload(durl_item):
    """Redis value for progressive proxy: {primary, backup} like DASH segments."""
    url = durl_item.get("url")
    backup = durl_item.get("backup_url") or durl_item.get("backupUrl") or []
    if isinstance(backup, str):
        backup = [backup]
    return orjson.dumps({"primary": url, "backup": backup})


async def formats_from_playurl(vid, idx, vi, data, ep_id=None):
    """Cache progressive URLs from an already-fetched playurl (no new playurl in parallel path)."""
    if not _has_durl(data):
        return None

    qn = data.get("quality", 64)
    await appredis.setex(
        f"mikuinv_{vid}_{idx}_{qn}", 1800, _progressive_url_payload(data["durl"][0])
    )

    ext = ".mp4" if ".mp4" in data["durl"][0]["url"].lower() else ""
    if ".flv" in data["durl"][0]["url"].lower():
        ext = ".flv"

    for f in data.get("support_formats", []):
        fq = f.get("quality")
        if fq is None or fq == qn:
            continue
        try:
            res = await video_get_src_for_qn(vi, idx, fq, ep_id=ep_id)
            if res and "durl" in res:
                await appredis.setex(
                    f"mikuinv_{vid}_{idx}_{fq}", 1800, _progressive_url_payload(res["durl"][0])
                )
        except Exception:
            pass

    return [
        {"quality": f["quality"], "new_description": f["new_description"], "ext": ext}
        for f in data.get("support_formats", [])
    ]


async def resolve_playback_from_parallel(vid, idx, vi, dash_data, mp4_data, ep_id=None):
    """
    Compare parallel DASH vs MP4 playurl results and pick player mode.
    Returns (use_dash: bool, payload) where payload is dash dict or supported_src list.
    """
    if mp4_data and mp4_data.get("dash"):
        if dash_data:
            dash_data = _merge_dash_data(dash_data, mp4_data)
        else:
            dash_data = mp4_data

    max_dash = _max_dash_video_qn(dash_data) if dash_data else 0
    mp4_qn = _playurl_granted_qn(mp4_data) if _has_durl(mp4_data) else 0
    print(f"[Video] Parallel compare: DASH max video id={max_dash}, MP4 best granted qn={mp4_qn}")

    if dash_data and max_dash >= 64:
        return True, dash_data

    if mp4_data and mp4_qn >= 64:
        formats = await formats_from_playurl(vid, idx, vi, mp4_data, ep_id=ep_id)
        if formats:
            return False, formats

    if dash_data:
        return True, dash_data

    if mp4_data and _has_durl(mp4_data):
        formats = await formats_from_playurl(vid, idx, vi, mp4_data, ep_id=ep_id)
        if formats:
            return False, formats

    return False, None


async def video_get_dash_for_qn(vi, idx, ep_id=None):
    """Get DASH playurl via WBI-signed API (bilibili_api Video.get_download_url)."""
    data = None
    try:
        data = await vi.get_download_url(page_index=idx)
        if isinstance(data, dict) and data.get("dash"):
            data = await _supplement_dash_qualities(vi, idx, data)
            ids = sorted({int(v.get("id", 0)) for v in data["dash"].get("video", [])})
            print(f"[Extra] DASH video qualities (id): {ids}")
            return data
        if isinstance(data, dict):
            print(f"[Extra] WBI playurl has no dash, keys={list(data.keys())[:12]}")
    except Exception as e:
        code = getattr(e, "code", None)
        if code == -404 or "404" in str(e):
            print("[Extra] PGC fallback for DASH (WBI -404)")
            pgc_data = await _pgc_dash_playurl(vi, idx, ep_id=ep_id)
            if pgc_data:
                return await _supplement_dash_qualities(vi, idx, pgc_data)
        print(f"[Extra] get_download_url failed: {code or type(e).__name__}: {e}")
        return {"code": code if code is not None else -1, "message": str(e)}

    pgc_data = await _pgc_dash_playurl(vi, idx, ep_id=ep_id)
    if pgc_data:
        return await _supplement_dash_qualities(vi, idx, pgc_data)
    return data if isinstance(data, dict) else {}


def generate_vod_master_m3u8(vid, idx, dash_data):
    """Generate HLS Master Playlist from DASH data."""
    if "dash" not in dash_data:
        return None

    master_m3u8 = ["#EXTM3U", "#EXT-X-VERSION:6"]

    # Audio Groups (Video/Audio/FLAC/Dolby)
    audio_tracks = dash_data["dash"].get("audio", [])
    if not audio_tracks and "flac" in dash_data["dash"] and dash_data["dash"]["flac"]:
        audio_tracks = dash_data["dash"]["flac"].get("audio", []) or [dash_data["dash"]["flac"].get("display", {})]

    for i, audio in enumerate(audio_tracks):
        # Handle cases where audio might be a dict with 'id' or 'quality'
        aid = audio.get("id") or audio.get("quality") or i
        cid = audio.get("codecid") or 0
        name = f"Audio {aid} (CID {cid})"
        uri = f"/video/m3u8/{vid}/{idx}/audio_{aid}_{cid}.m3u8"
        group_id = 'GROUP-ID="audio"'
        default = f"DEFAULT={'YES' if i == 0 else 'NO'}"
        master_m3u8.append(f'#EXT-X-MEDIA:TYPE=AUDIO,{group_id},NAME="{name}",AUTOSELECT=YES,{default},URI="{uri}"')

    # Video Variants
    for video_track in dash_data["dash"].get("video", []):
        bandwidth = video_track.get("bandwidth", 0)
        resolution = f"{video_track.get('width')}x{video_track.get('height')}"
        codecs = video_track.get("codecs") or video_track.get("codec") or "avc1.64001F"
        qn = video_track["id"]
        cid = video_track.get("codecid") or 0
        uri = f"/video/m3u8/{vid}/{idx}/video_{qn}_{cid}.m3u8"
        master_m3u8.append(
            f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution},CODECS="{codecs}",AUDIO="audio"'
        )
        master_m3u8.append(uri)

    return "\n".join(master_m3u8)


def generate_vod_mpd(vid, idx, dash_data):
    """Generate DASH MPD from Bilibili DASH data."""
    if "dash" not in dash_data:
        return None

    dash = dash_data["dash"]
    duration = dash.get("duration", 0)
    min_buffer_time = dash.get("minBufferTime", 1.5)

    mpd = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static" mediaPresentationDuration="PT{}S" minBufferTime="PT{}S">'.format(
            duration, min_buffer_time
        ),
        '  <Period id="1" start="PT0S">',
    ]

    # Video Adaptation Set
    mpd.append('    <AdaptationSet id="1" mimeType="video/mp4" segmentAlignment="true" startWithSAP="1">')
    for video in dash.get("video", []):
        qn = video["id"]
        cid = video.get("codecid") or 0
        bandwidth = video.get("bandwidth", 0)
        width = video.get("width", 0)
        height = video.get("height", 0)
        frame_rate = video.get("frameRate", "24")
        codecs = video.get("codecs") or "avc1.64001F"

        # SegmentBase info
        sb = video.get("SegmentBase", {})
        init_range = sb.get("Initialization", "0-999")
        index_range = sb.get("indexRange", "1000-2000")
        pto = sb.get("presentationTimeOffset", 0)

        # Calculate timescale from duration if possible
        track_duration = video.get("duration", 0)
        timescale = (track_duration // duration) if duration and track_duration else 90000

        mpd.append(
            '      <Representation id="video_{}_{}" codecs="{}" bandwidth="{}" width="{}" height="{}" frameRate="{}">'.format(
                qn, cid, codecs, bandwidth, width, height, frame_rate
            )
        )
        mpd.append("        <BaseURL>/proxy/dash/{}/{}/video/{}/{}</BaseURL>".format(vid, idx, qn, cid))
        mpd.append(
            '        <SegmentBase indexRange="{}" presentationTimeOffset="{}" timescale="{}">'.format(
                index_range, pto, timescale
            )
        )
        mpd.append('          <Initialization range="{}"/>'.format(init_range))
        mpd.append("        </SegmentBase>")
        mpd.append("      </Representation>")
    mpd.append("    </AdaptationSet>")

    # Audio Adaptation Set
    mpd.append('    <AdaptationSet id="2" mimeType="audio/mp4" segmentAlignment="true" startWithSAP="1">')
    audio_tracks = dash.get("audio", [])
    if not audio_tracks and "flac" in dash and dash["flac"]:
        audio_tracks = dash["flac"].get("audio", []) or [dash["flac"].get("display", {})]

    for audio in audio_tracks:
        qn = audio.get("id") or audio.get("quality") or 0
        cid = audio.get("codecid") or 0
        bandwidth = audio.get("bandwidth", 0)
        codecs = audio.get("codecs") or "mp4a.40.2"

        sb = audio.get("SegmentBase", {})
        init_range = sb.get("Initialization", "0-999")
        index_range = sb.get("indexRange", "1000-2000")
        pto = sb.get("presentationTimeOffset", 0)

        # Calculate timescale from duration if possible
        track_duration = audio.get("duration", 0)
        timescale = (track_duration // duration) if duration and track_duration else 44100

        mpd.append(
            '      <Representation id="audio_{}_{}" codecs="{}" bandwidth="{}">'.format(qn, cid, codecs, bandwidth)
        )
        mpd.append("        <BaseURL>/proxy/dash/{}/{}/audio/{}/{}</BaseURL>".format(vid, idx, qn, cid))
        mpd.append(
            '        <SegmentBase indexRange="{}" presentationTimeOffset="{}" timescale="{}">'.format(
                index_range, pto, timescale
            )
        )
        mpd.append('          <Initialization range="{}"/>'.format(init_range))
        mpd.append("        </SegmentBase>")
        mpd.append("      </Representation>")
    mpd.append("    </AdaptationSet>")

    mpd.append("  </Period>")
    mpd.append("</MPD>")

    return "\n".join(mpd)


def generate_vod_media_m3u8(dash_data, media_type, qn, cid, duration, vid, idx):
    """Generate HLS Media Playlist for a specific quality."""
    if "dash" not in dash_data:
        return None

    dash = dash_data["dash"]
    media_list = dash.get("video" if media_type == "video" else "audio", [])
    target_media = next(
        (m for m in media_list if str(m.get("id")) == str(qn) and str(m.get("codecid", 0)) == str(cid)), None
    )
    if not target_media and media_type == "audio" and "flac" in dash:
        target_media = next(
            (
                m
                for m in dash["flac"].get("audio", [])
                if str(m.get("id")) == str(qn) and str(m.get("codecid", 0)) == str(cid)
            ),
            None,
        )

    if not target_media:
        return None

    init_range_raw = target_media.get("SegmentBase", {}).get("Initialization", "0-999")
    try:
        start, end = map(int, init_range_raw.split("-"))
        length = end - start + 1
        init_range = f"{length}@{start}"
    except Exception:
        init_range = init_range_raw

    playlist = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{int(duration) + 1}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        f'#EXT-X-MAP:URI="/proxy/dash/{vid}/{idx}/{media_type}/{qn}/{cid}",BYTERANGE="{init_range}"',
        f"#EXTINF:{duration},",
        f"/proxy/dash/{vid}/{idx}/{media_type}/{qn}/{cid}",
        "#EXT-X-ENDLIST",
    ]

    return "\n".join(playlist)


# The following algorithm is adopted from bilibili-API-collect.
# https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/other/bvid_desc.md

table = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
itable = {table[i]: i for i in range(len(table))}

s = [11, 10, 3, 8, 4, 6]
XOR = 177451812
ADD = 8728348608


def bv2av(x):
    r = 0
    for i in range(6):
        r += itable[x[s[i]]] * 58**i
    return (r - ADD) ^ XOR


def av2bv(x):
    try:
        x = int(x[2:] if str(x).startswith("av") else x)
        x = (x ^ XOR) + ADD
        r = list("BV1  4 1 7  ")
        for i in range(6):
            r[s[i]] = table[x // 58**i % 58]
        return "".join(r)
    except ValueError:
        raise ArgsException("avid 提供错误，必须是以 av 开头的数字组成的字符串。") from None
