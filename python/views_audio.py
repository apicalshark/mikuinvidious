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


import asyncio

from api import audio, comment
from quart import Response, request
from shared import (
    app,
    appcred,
    appredis,
    cache_get,
    cache_minutes,
    cache_set,
    render_template_with_theme,
)


@app.route("/audio/<auid>")
async def audio_view(auid):
    ato = request.args.get("ato") == "1"
    auid_int = int(auid[2:]) if auid.startswith("au") else int(auid)

    async def get_audio_url():
        if not await appredis.exists(f"mikuinv_{auid}_{0}_0"):
            try:
                asrc = await a.get_download_url()
                if "cdns" in asrc and asrc["cdns"]:
                    await appredis.setex(f"mikuinv_{auid}_{0}_0", 1800, asrc["cdns"][0])
                elif "url" in asrc:
                    await appredis.setex(f"mikuinv_{auid}_{0}_0", 1800, asrc["url"])
            except Exception:
                pass

    cache_ttl = cache_minutes("audio_minutes") * 60
    cache_key = f"audio:data:{auid}"
    cache_hit = False
    ainfo = None
    acomments = {"page": {"count": 0}, "replies": []}
    if cache_ttl > 0:
        data = await cache_get(cache_key, cache_ttl)
        if isinstance(data, dict) and isinstance(data.get("ainfo"), dict) and data.get("ainfo"):
            ainfo = data["ainfo"]
            cache_hit = True
    if cache_hit:
        # Info comes from cache; comments stay live so they never go stale.
        try:
            acomments = await comment.get_comments(
                auid_int, comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE
            )
        except Exception:
            pass
    else:
        a = audio.Audio(auid_int, credential=appcred)
        results = await asyncio.gather(
            a.get_info(),
            comment.get_comments(auid_int, comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE),
            get_audio_url(),
            return_exceptions=True,
        )
        ainfo = results[0] if isinstance(results[0], dict) else {}
        acomments = results[1] if isinstance(results[1], dict) else {"page": {"count": 0}, "replies": []}
        if cache_ttl > 0 and isinstance(ainfo, dict) and ainfo:
            await cache_set(cache_key, {"ainfo": ainfo}, cache_ttl)
    vinfo = {
        "title": ainfo.get("title", auid),
        "pic": ainfo.get("cover", ""),
        "desc": ainfo.get("intro", ""),
        "owner": {"name": ainfo.get("author", "Unknown"), "mid": ainfo.get("mid", 0), "face": ""},
        "stat": {
            "view": ainfo.get("statistic", {}).get("play", 0),
            "like": ainfo.get("statistic", {}).get("collect", 0),
            "coin": ainfo.get("statistic", {}).get("coin", 0),
            "favorite": ainfo.get("statistic", {}).get("collect", 0),
            "share": ainfo.get("statistic", {}).get("share", 0),
        },
        "pubdate": ainfo.get("passtime", 0),
        "bvid": auid,
        "tid": 0,
        "tname": "Audio",
    }
    html = await render_template_with_theme(
        "video_listen.html",
        vid=auid,
        vinfo=vinfo,
        vrelated=[],
        vcomments=acomments,
        keywords="",
        ato=ato,
        idx=0,
        vset=[{"page": 1, "part": auid}],
    )
    if cache_ttl > 0:
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT" if cache_hit else "MISS"})
    return html


@app.route("/audio_list/<amid>")
@app.route("/audio_list/<amid>:<idx>")
async def audio_list_view(amid, idx=0):
    idx, ato = int(idx), request.args.get("ato") == "1"
    amid_int = int(amid[2:]) if amid.startswith("am") else int(amid)
    cache_ttl = cache_minutes("audio_minutes") * 60
    cache_key = f"audiolist:data:{amid}:{idx}"
    cache_hit = False
    songs = ainfo = list_info = None
    acomments = {"page": {"count": 0}, "replies": []}
    if cache_ttl > 0:
        data = await cache_get(cache_key, cache_ttl)
        cached_songs = data.get("songs") if isinstance(data, dict) else None
        if (
            isinstance(data, dict)
            and isinstance(cached_songs, list)
            and cached_songs
            and all(isinstance(s, dict) and "id" in s for s in cached_songs)
            and isinstance(data.get("ainfo"), dict)
            and data.get("ainfo")
            and isinstance(data.get("list_info"), dict)
        ):
            songs, ainfo, list_info = cached_songs, data["ainfo"], data["list_info"]
            cache_hit = True
    if cache_hit:
        if idx >= len(songs):
            return await render_template_with_theme("error.html", status="Playlist empty", desc="No songs found"), 404
        current_song = songs[idx]
        # Track info comes from cache; comments stay live so they never go stale.
        try:
            acomments = await comment.get_comments(
                current_song["id"], comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE
            )
        except Exception:
            pass
        auid = f"au{current_song['id']}"
    else:
        al = audio.AudioList(amid_int, credential=appcred)
        songs_res = await al.get_song_list()
        songs = songs_res.get("data", [])
        if not songs or idx >= len(songs):
            return await render_template_with_theme("error.html", status="Playlist empty", desc="No songs found"), 404
        current_song = songs[idx]
        auid = f"au{current_song['id']}"
        a = audio.Audio(current_song["id"], credential=appcred)

        async def get_audio_url():
            if not await appredis.exists(f"mikuinv_{amid}_{idx}_0"):
                try:
                    asrc = await a.get_download_url()
                    if "cdns" in asrc and asrc["cdns"]:
                        await appredis.setex(f"mikuinv_{amid}_{idx}_0", 1800, asrc["cdns"][0])
                    elif "url" in asrc:
                        await appredis.setex(f"mikuinv_{amid}_{idx}_0", 1800, asrc["url"])
                except Exception:
                    pass

        results = await asyncio.gather(
            a.get_info(),
            al.get_info(),
            comment.get_comments(current_song["id"], comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE),
            get_audio_url(),
            return_exceptions=True,
        )
        ainfo, list_info = (
            results[0] if isinstance(results[0], dict) else {},
            results[1] if isinstance(results[1], dict) else {},
        )
        acomments = results[2] if isinstance(results[2], dict) else {"page": {"count": 0}, "replies": []}
        if cache_ttl > 0 and songs and isinstance(ainfo, dict) and ainfo:
            await cache_set(cache_key, {"songs": songs, "ainfo": ainfo, "list_info": list_info}, cache_ttl)
    vinfo = {
        "title": ainfo.get("title", auid),
        "pic": ainfo.get("cover", ""),
        "desc": ainfo.get("intro", ""),
        "owner": {"name": ainfo.get("author", "Unknown"), "mid": ainfo.get("mid", 0), "face": ""},
        "stat": {
            "view": ainfo.get("statistic", {}).get("play", 0),
            "like": ainfo.get("statistic", {}).get("collect", 0),
            "coin": ainfo.get("statistic", {}).get("coin", 0),
            "favorite": ainfo.get("statistic", {}).get("collect", 0),
            "share": ainfo.get("statistic", {}).get("share", 0),
        },
        "pubdate": ainfo.get("passtime", 0),
        "bvid": auid,
        "tid": 0,
        "tname": list_info.get("title", "Audio List"),
    }
    vset = [
        {
            "page": i + 1,
            "part": s.get("title", f"Song {i + 1}"),
            "duration": s.get("duration", 0),
            "first_frame": s.get("cover", ""),
        }
        for i, s in enumerate(songs)
        if isinstance(s, dict)
    ]
    html = await render_template_with_theme(
        "video_listen.html",
        vid=amid,
        vinfo=vinfo,
        vrelated=[],
        vcomments=acomments,
        keywords="",
        ato=ato,
        idx=idx,
        vset=vset,
    )
    if cache_ttl > 0:
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT" if cache_hit else "MISS"})
    return html
