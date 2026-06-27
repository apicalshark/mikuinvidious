import asyncio
import re
import warnings
from urllib.parse import urlparse

import httpx
import orjson
from bilibili_api.utils.network import Api
from quart import Blueprint, Response, request
from rate_limit import RATE_LIMITS, rate_limit
from shared import Network, app, appconf, appcred, appredis, get_common_headers, safe_json_loads

warnings.warn(
    "dash_proxy module is deprecated and will be removed in a future release. "
    "DASH playback has been replaced with direct FLV/MP4 streaming via /proxy/video/. "
    "If you rely on DASH/MPD/HLS manifests, migrate before the next major version.",
    DeprecationWarning,
    stacklevel=2,
)

dash_proxy_bp = Blueprint("dash_proxy", __name__)

_ALLOWED_DASH_DOMAINS = [
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
]


def _is_safe_dash_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    if not any(hostname == d.lstrip(".") or hostname.endswith(d) for d in _ALLOWED_DASH_DOMAINS):
        return False
    return True


async def video_get_dash_for_qn(vi, idx, ep_id=None):
    warnings.warn(
        "video_get_dash_for_qn is deprecated. Use video_get_src_for_qn from extra instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    cid = await vi.get_cid(idx)
    api = Api(
        "https://api.bilibili.com/x/player/playurl",
        "GET",
        verify=(not not vi.credential.sessdata),
        json_body=True,
        credential=vi.credential,
    )
    api.params = {"avid": vi.get_aid(), "cid": cid, "fnval": "4048", "platform": "html5", "high_quality": 1}

    res = {}
    try:
        res = await api.request()
    except Exception as e:
        if hasattr(e, "code") and e.code == -404:
            print("[DashProxy] PGC Fallback for DASH (caught exception)")
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
                    if isinstance(res_node, dict) and "dash" in res_node:
                        return res_node
                    return pgc_res
            except Exception as pgc_e:
                print(f"[DashProxy] PGC Fallback DASH failed: {pgc_e}")
        print(f"[DashProxy] Original API failed: {e}")
        return {"code": getattr(e, "code", -1), "message": str(e)}

    if "data" in res:
        return res["data"]
    return res


async def populate_dash_redis(vid, idx, dash_data):
    warnings.warn(
        "populate_dash_redis is deprecated. DASH Redis population is no longer needed.",
        DeprecationWarning,
        stacklevel=2,
    )
    if not dash_data or "dash" not in dash_data:
        return
    for mt in ["video", "audio"]:
        tracks = dash_data["dash"].get(mt, [])
        if mt == "audio" and not tracks and "flac" in dash_data["dash"]:
            tracks = dash_data["dash"]["flac"].get("audio", [])
        for item in tracks:
            url = item.get("baseUrl") or item.get("base_url")
            qn = item.get("id")
            cid = item.get("codecid") or 0
            if url and qn is not None:
                await appredis.setex(f"miku_dash_url_{vid}_{idx}_{mt}_{qn}_{cid}", 1800, url)


def generate_vod_master_m3u8(vid, idx, dash_data):
    warnings.warn(
        "generate_vod_master_m3u8 is deprecated. HLS master playlist generation for DASH is no longer supported.",
        DeprecationWarning,
        stacklevel=2,
    )
    if "dash" not in dash_data:
        return None

    master_m3u8 = ["#EXTM3U", "#EXT-X-VERSION:6"]

    audio_tracks = dash_data["dash"].get("audio", [])
    if not audio_tracks and "flac" in dash_data["dash"] and dash_data["dash"]["flac"]:
        audio_tracks = dash_data["dash"]["flac"].get("audio", []) or [dash_data["dash"]["flac"].get("display", {})]

    for i, audio in enumerate(audio_tracks):
        aid = audio.get("id") or audio.get("quality") or i
        cid = audio.get("codecid") or 0
        name = f"Audio {aid} (CID {cid})"
        uri = f"/video/m3u8/{vid}/{idx}/audio_{aid}_{cid}.m3u8"
        group_id = 'GROUP-ID="audio"'
        default = f"DEFAULT={'YES' if i == 0 else 'NO'}"
        master_m3u8.append(f'#EXT-X-MEDIA:TYPE=AUDIO,{group_id},NAME="{name}",AUTOSELECT=YES,{default},URI="{uri}"')

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
    warnings.warn(
        "generate_vod_mpd is deprecated. DASH MPD manifest generation is no longer supported.",
        DeprecationWarning,
        stacklevel=2,
    )
    if "dash" not in dash_data:
        return None

    dash = dash_data["dash"]
    duration = dash.get("duration", 0)
    min_buffer_time = dash.get("minBufferTime", 1.5)

    mpd = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static" mediaPresentationDuration="PT{duration}S" minBufferTime="PT{min_buffer_time}S">',
        '  <Period id="1" start="PT0S">',
    ]

    mpd.append('    <AdaptationSet id="1" mimeType="video/mp4" segmentAlignment="true" startWithSAP="1">')
    for video in dash.get("video", []):
        qn = video["id"]
        cid = video.get("codecid") or 0
        bandwidth = video.get("bandwidth", 0)
        width = video.get("width", 0)
        height = video.get("height", 0)
        frame_rate = video.get("frameRate", "24")
        codecs = video.get("codecs") or "avc1.64001F"

        sb = video.get("SegmentBase", {})
        init_range = sb.get("Initialization", "0-999")
        index_range = sb.get("indexRange", "1000-2000")
        pto = sb.get("presentationTimeOffset", 0)

        track_duration = video.get("duration", 0)
        timescale = (track_duration // duration) if duration and track_duration else 90000

        mpd.append(
            f'      <Representation id="video_{qn}_{cid}" codecs="{codecs}" bandwidth="{bandwidth}" width="{width}" height="{height}" frameRate="{frame_rate}">'
        )
        mpd.append(f"        <BaseURL>/proxy/dash/{vid}/{idx}/video/{qn}/{cid}</BaseURL>")
        mpd.append(
            f'        <SegmentBase indexRange="{index_range}" presentationTimeOffset="{pto}" timescale="{timescale}">'
        )
        mpd.append(f'          <Initialization range="{init_range}"/>')
        mpd.append("        </SegmentBase>")
        mpd.append("      </Representation>")
    mpd.append("    </AdaptationSet>")

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

        track_duration = audio.get("duration", 0)
        timescale = (track_duration // duration) if duration and track_duration else 44100

        mpd.append(
            f'      <Representation id="audio_{qn}_{cid}" codecs="{codecs}" bandwidth="{bandwidth}">'
        )
        mpd.append(f"        <BaseURL>/proxy/dash/{vid}/{idx}/audio/{qn}/{cid}</BaseURL>")
        mpd.append(
            f'        <SegmentBase indexRange="{index_range}" presentationTimeOffset="{pto}" timescale="{timescale}">'
        )
        mpd.append(f'          <Initialization range="{init_range}"/>')
        mpd.append("        </SegmentBase>")
        mpd.append("      </Representation>")
    mpd.append("    </AdaptationSet>")

    mpd.append("  </Period>")
    mpd.append("</MPD>")

    return "\n".join(mpd)


def generate_vod_media_m3u8(dash_data, media_type, qn, cid, duration, vid, idx):
    warnings.warn(
        "generate_vod_media_m3u8 is deprecated. HLS media playlist generation for DASH is no longer supported.",
        DeprecationWarning,
        stacklevel=2,
    )
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


@dash_proxy_bp.route("/proxy/dash/<vid>/<int:idx>/<media_type>/<int:qn>/<int:cid>")
@rate_limit(**RATE_LIMITS["proxy"])
async def proxy_dash(vid, idx, media_type, qn, cid):
    warnings.warn(
        "DASH proxy endpoint is deprecated. Use /proxy/video/ instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from shared import TicketManager

    url = await appredis.get(f"miku_dash_url_{vid}_{idx}_{media_type}_{qn}_{cid}")
    if not url:
        return Response("Not Found", status=404)

    if isinstance(url, bytes):
        url = url.decode()

    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)

    if not _is_safe_dash_url(url):
        return Response("Forbidden: Invalid proxy target", status=403)

    creds = appconf["credential"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

    headers = get_common_headers(appconf["bili"]).copy()

    ticket = await TicketManager.get_ticket()
    if ticket:
        headers["x-bili-ticket"] = ticket

    headers["session_id"] = TicketManager._generate_session_id()
    headers["x-bili-trace-id"] = TicketManager._generate_trace_id()

    if appconf["credential"].get("buvid3"):
        headers["buvid"] = appconf["credential"]["buvid3"]
    if appconf["credential"].get("buvid4"):
        headers["buvid4"] = appconf["credential"]["buvid4"]

    for k, v in request.headers.items():
        if k.lower() in ["range", "if-range", "x-playback-session-id", "if-modified-since", "if-none-match"]:
            headers[k.lower()] = v

    client = await Network.get_async_client()
    resp = None
    try:
        proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
        resp = await client.send(proxy_request, stream=True, follow_redirects=True)

        if resp.status_code in [403, 412, 514]:
            print(f"[DashProxy] proxy_dash upstream returned status {resp.status_code}. Refreshing ticket and retrying...")
            await resp.aclose()
            ticket = await TicketManager.get_ticket(force_refresh=True)
            if ticket:
                headers["x-bili-ticket"] = ticket
            else:
                headers.pop("x-bili-ticket", None)
            headers["session_id"] = TicketManager._generate_session_id()
            headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            resp = await client.send(proxy_request, stream=True, follow_redirects=True)

        async def generate():
            try:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 64):
                    yield chunk
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.StreamClosed):
                pass
            finally:
                await resp.aclose()

        proxy_resp = Response(
            generate(),
            status=resp.status_code,
        )
        proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
        proxy_resp.headers["X-Accel-Buffering"] = "no"

        for k, v in resp.headers.items():
            k_lower = k.lower()
            if k_lower in [
                "content-type",
                "content-length",
                "content-range",
                "accept-ranges",
                "etag",
                "last-modified",
                "cache-control",
            ]:
                if k_lower in ("content-length", "content-range"):
                    continue
                proxy_resp.headers[k] = v

        if resp.status_code in [200, 206]:
            current_ct = proxy_resp.headers.get("Content-Type", "").lower()
            if not current_ct or "application/octet-stream" in current_ct:
                if media_type == "video":
                    proxy_resp.headers["Content-Type"] = "video/mp4"
                else:
                    proxy_resp.headers["Content-Type"] = "audio/mp4"

        return proxy_resp
    except Exception as e:
        print(f"[DashProxy] Error in proxy_dash: {e}")
        if resp:
            await resp.aclose()
        return Response(str(e), status=502)


@app.route("/video/dash/<vid>/<int:idx>/manifest.mpd")
@rate_limit(**RATE_LIMITS["proxy"])
async def video_dash_manifest_view(vid, idx):
    warnings.warn(
        "DASH MPD manifest endpoint is deprecated. DASH playback is no longer supported.",
        DeprecationWarning,
        stacklevel=2,
    )
    from bilibili_api import video

    v = video.Video(bvid=vid, credential=appcred)
    dash_cache = await appredis.get(f"miku_dash_{vid}_{idx}")
    if dash_cache:
        dash_data = safe_json_loads(dash_cache)
        if dash_data is None:
            dash_cache = None
    if not dash_cache:
        try:
            dash_data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=10.0)
            await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(dash_data))
        except Exception:
            return "Upstream Timeout", 504

    await populate_dash_redis(vid, idx, dash_data)
    mpd_content = generate_vod_mpd(vid, idx, dash_data)
    if not mpd_content:
        return "Not Found", 404
    return Response(mpd_content, content_type="application/dash+xml")


@app.route("/video/m3u8/<vid>/<int:idx>/master.m3u8")
@rate_limit(**RATE_LIMITS["proxy"])
async def video_master_m3u8_view(vid, idx):
    warnings.warn(
        "HLS master playlist endpoint is deprecated. HLS playback via DASH conversion is no longer supported.",
        DeprecationWarning,
        stacklevel=2,
    )
    from bilibili_api import video

    v = video.Video(bvid=vid, credential=appcred)
    dash_cache = await appredis.get(f"miku_dash_{vid}_{idx}")
    if dash_cache:
        dash_data = safe_json_loads(dash_cache)
        if dash_data is None:
            dash_cache = None
    if not dash_cache:
        try:
            dash_data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=10.0)
            await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(dash_data))
        except Exception:
            return "Upstream Timeout", 504

    await populate_dash_redis(vid, idx, dash_data)
    m3u8_content = generate_vod_master_m3u8(vid, idx, dash_data)
    if not m3u8_content:
        return "Not Found", 404
    return Response(m3u8_content, content_type="application/vnd.apple.mpegurl")


@app.route("/video/m3u8/<vid>/<int:idx>/<media_type>_<int:qn>_<int:cid>.m3u8")
@rate_limit(**RATE_LIMITS["proxy"])
async def video_media_m3u8_view(vid, idx, media_type, qn, cid):
    warnings.warn(
        "HLS media playlist endpoint is deprecated. HLS playback via DASH conversion is no longer supported.",
        DeprecationWarning,
        stacklevel=2,
    )
    from bilibili_api import video

    v = video.Video(bvid=vid, credential=appcred)
    dash_cache = await appredis.get(f"miku_dash_{vid}_{idx}")
    if dash_cache:
        dash_data = safe_json_loads(dash_cache)
        if dash_data is None:
            dash_cache = None
    if not dash_cache:
        try:
            dash_data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=10.0)
            await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(dash_data))
        except Exception:
            return "Upstream Timeout", 504

    await populate_dash_redis(vid, idx, dash_data)
    vinfo = await v.get_info()
    m3u8_content = generate_vod_media_m3u8(dash_data, media_type, qn, cid, vinfo.get("duration", 0), vid, idx)
    if not m3u8_content:
        return "Not Found", 404
    return Response(m3u8_content, content_type="application/vnd.apple.mpegurl")


async def get_dash_play_info(vid, idx, v, ep_id=None):
    warnings.warn(
        "get_dash_play_info is deprecated. Use the legacy race-mode player info from dash_proxy.",
        DeprecationWarning,
        stacklevel=2,
    )
    has_dash, v_supported_src = False, []

    cached_dash = await appredis.get(f"miku_dash_{vid}_{idx}")
    if cached_dash:
        dash_data = safe_json_loads(cached_dash)
        if dash_data:
            has_dash = True
            v_supported_src = [
                {"quality": f["quality"], "new_description": f["new_description"]}
                for f in dash_data.get("support_formats", [])
            ]
            return has_dash, v_supported_src

    async def fetch_dash_task():
        try:
            data = await asyncio.wait_for(video_get_dash_for_qn(v, idx, ep_id=ep_id), timeout=4.0)
            if "dash" in data:
                await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(data))
                await populate_dash_redis(vid, idx, data)
                return ("dash", data)
        except Exception:
            pass
        return ("dash", None)

    async def fetch_fallback_task():
        from extra import video_get_src_for_qn

        try:
            data = await asyncio.wait_for(video_get_src_for_qn(v, idx, ep_id=ep_id), timeout=4.0)
            if data and "durl" in data:
                url = data["durl"][0]["url"]
                qn = data.get("quality", 16)
                await appredis.setex(f"mikuinv_{vid}_{idx}_{qn}", 1800, url)
                await appredis.setex(f"mikuinv_{vid}_{idx}", 1800, url)

                support_formats = data.get("support_formats", [])
                if support_formats:
                    first_qn = support_formats[0]["quality"]
                    if first_qn != qn:
                        try:
                            res_high = await asyncio.wait_for(video_get_src_for_qn(v, idx, first_qn), timeout=4.0)
                            if "durl" in res_high:
                                await appredis.setex(
                                    f"mikuinv_{vid}_{idx}_{first_qn}", 1800, res_high["durl"][0]["url"]
                                )
                        except Exception:
                            pass
                return ("fallback", data)
        except Exception:
            pass
        return ("fallback", None)

    t_dash = asyncio.create_task(fetch_dash_task())
    t_fallback = asyncio.create_task(fetch_fallback_task())
    pending = {t_dash, t_fallback}

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                result_type, result_data = task.result()
                if result_type == "dash" and result_data:
                    has_dash = True
                    v_supported_src = [
                        {"quality": f["quality"], "new_description": f["new_description"]}
                        for f in result_data.get("support_formats", [])
                    ]
                    if v_supported_src:
                        first_qn = v_supported_src[0]["quality"]
                        if not await appredis.exists(f"mikuinv_{vid}_{idx}_{first_qn}"):
                            asyncio.create_task(video_get_src_for_qn(v, idx, first_qn))
                    return has_dash, v_supported_src
                elif result_type == "fallback" and result_data:
                    ext = ""
                    if "durl" in result_data and result_data["durl"]:
                        first_url = result_data["durl"][0]["url"]
                        if ".flv" in first_url.lower():
                            ext = ".flv"
                        elif ".mp4" in first_url.lower():
                            ext = ".mp4"

                    v_supported_src = [
                        {"quality": f["quality"], "new_description": f["new_description"], "ext": ext}
                        for f in result_data.get("support_formats", [])
                    ]
                    return False, v_supported_src
            except Exception:
                continue
    return False, []


async def precache_dash(vid, idx, v):
    warnings.warn(
        "precache_dash is deprecated. DASH precaching is no longer needed for video playback.",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        data = await asyncio.wait_for(video_get_dash_for_qn(v, idx), timeout=4.0)
        if "dash" in data:
            await appredis.setex(f"miku_dash_{vid}_{idx}", 1800, orjson.dumps(data))
            await populate_dash_redis(vid, idx, data)
    except Exception as e:
        print(f"[DashProxy] Pre-cache DASH failed for {vid}: {e}")
