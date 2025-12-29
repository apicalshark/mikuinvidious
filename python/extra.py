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
import hashlib
import json
import re
import time
import urllib.parse

from bilibili_api.exceptions import ArgsException
from bilibili_api.utils.network import Api
from bs4 import BeautifulSoup


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
        state = json.loads(match.group(1))
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
                state = json.loads(match.group(1))
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


import hashlib
import time
from bilibili_api.utils.network import Api
from shared import appredis

mixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

import urllib.parse

def get_mixin_key(orig):
    """Generate mixin key for WBI."""
    return "".join([orig[i] for i in mixinKeyEncTab])[:32]

async def get_wbi_keys():
    """Fetch img_key and sub_key from Bilibili API, cached in Redis."""
    cached = appredis.get("miku_wbi_keys")
    if cached:
        return json.loads(cached)
    
    api = Api("https://api.bilibili.com/x/web-interface/nav", "GET")
    res = await api.request()
    img_url = res["wbi_img"]["img_url"]
    sub_url = res["wbi_img"]["sub_url"]
    img_key = img_url.split("/")[-1].split(".")[0]
    sub_key = sub_url.split("/")[-1].split(".")[0]
    
    keys = {"img_key": img_key, "sub_key": sub_key}
    appredis.setex("miku_wbi_keys", 3600, json.dumps(keys))
    return keys

async def sign_wbi(params):
    """Sign parameters with WBI (Aligned with yt-dlp)."""
    keys = await get_wbi_keys()
    mixin_key = get_mixin_key(keys["img_key"] + keys["sub_key"])
    
    params["wts"] = round(time.time())
    
    # Character filtering and sorting as per yt-dlp
    filtered_params = {
        k: "".join(filter(lambda char: char not in "!'()*", str(v)))
        for k, v in sorted(params.items())
    }
    
    query = urllib.parse.urlencode(filtered_params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    
    params["w_rid"] = w_rid
    print(f"[WBI] Signed query: {query} | w_rid: {w_rid}")
    return params

async def video_get_src_for_qn(vi, idx, quality=16):
    """Get a specific available source for video."""
    cid = await vi.get_cid(idx)
    params = {
        "avid": vi.get_aid(),
        "cid": cid,
        "qn": quality,
        "platform": "html5",
        "high_quality": 1,
        "try_look": 1
    }
    
    # Use WBI only if logged in, otherwise use plain playurl
    if vi.credential and vi.credential.sessdata:
        endpoint = "https://api.bilibili.com/x/player/wbi/playurl"
        params = await sign_wbi(params)
    else:
        endpoint = "https://api.bilibili.com/x/player/playurl"
    
    api = Api(
        endpoint,
        "GET",
        verify=(not not (vi.credential and vi.credential.sessdata)),
        credential=vi.credential,
    )
    api.params = params
    res = await api.request()
    print(f"[API] src response ({'WBI' if 'wbi' in endpoint else 'Plain'}) for {vi.get_aid()}: {str(res)[:200]}...")
    return res

async def video_get_dash_for_qn(vi, idx):
    """Get a specific available source for video."""
    cid = await vi.get_cid(idx)
    params = {
        "avid": vi.get_aid(),
        "cid": cid,
        "fnval": "4048",
        "platform": "html5",
        "high_quality": 1,
        "try_look": 1
    }
    
    if vi.credential and vi.credential.sessdata:
        endpoint = "https://api.bilibili.com/x/player/wbi/playurl"
        params = await sign_wbi(params)
    else:
        endpoint = "https://api.bilibili.com/x/player/playurl"
        
    api = Api(
        endpoint,
        "GET",
        verify=(not not (vi.credential and vi.credential.sessdata)),
        json_body=True,
        credential=vi.credential,
    )
    api.params = params
    res = await api.request()
    print(f"[API] dash response ({'WBI' if 'wbi' in endpoint else 'Plain'}) for {vi.get_aid()}: {str(res)[:200]}...")
    return res


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
        name = f"Audio {aid}"
        uri = f"/video/m3u8/{vid}/{idx}/audio_{aid}.m3u8"
        group_id = 'GROUP-ID="audio"'
        default = f"DEFAULT={'YES' if i == 0 else 'NO'}"
        master_m3u8.append(f'#EXT-X-MEDIA:TYPE=AUDIO,{group_id},NAME="{name}",AUTOSELECT=YES,{default},URI="{uri}"')

    # Video Variants
    for video in dash_data["dash"].get("video", []):
        bandwidth = video.get("bandwidth", 0)
        resolution = f"{video.get('width')}x{video.get('height')}"
        codecs = video.get("codecs", "avc1.64001F")
        uri = f"/video/m3u8/{vid}/{idx}/video_{video['id']}.m3u8"
        master_m3u8.append(
            f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution},CODECS="{codecs}",AUDIO="audio"'
        )
        master_m3u8.append(uri)

    return "\n".join(master_m3u8)


def generate_vod_media_m3u8(dash_data, media_type, qn, duration):
    """Generate HLS Media Playlist for a specific quality."""
    if "dash" not in dash_data:
        return None

    # Check all possible media containers
    dash = dash_data["dash"]
    media_list = dash.get("video" if media_type == "video" else "audio", [])

    # Fallback search in flac/dolby if not found in standard tracks
    target_media = next((m for m in media_list if str(m.get("id")) == str(qn)), None)
    if not target_media and media_type == "audio":
        if "flac" in dash and dash["flac"] and dash["flac"].get("audio"):
            target_media = next((m for m in dash["flac"]["audio"] if str(m.get("id")) == str(qn)), None)

    if not target_media:
        return None

    # Initialization and Data URL handling (support both baseUrl and base_url)
    init_range = target_media.get("SegmentBase", {}).get("Initialization", "0-999")
    # Bilibili normally has one segment after initialization.
    # We omit BYTERANGE for the main data to allow the player to fetch the rest of the stream.

    playlist = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{int(duration) + 1}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        f'#EXT-X-MAP:URI="/proxy/dash/{media_type}/{qn}",BYTERANGE="{init_range}"',
        f"#EXTINF:{duration},",
        f"/proxy/dash/{media_type}/{qn}",
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
