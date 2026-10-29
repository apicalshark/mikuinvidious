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


from dash_fetch import _load_dash_data, _normalize_track_urls
from quart import Response, request
from rate_limit import RATE_LIMITS, rate_limit
from shared import app, appredis


try:
    from miku_core_rs import generate_vod_mpd as rs_generate_vod_mpd
except ImportError:
    rs_generate_vod_mpd = None


def _sb_initialization_range(sb: dict) -> str:
    """Extract the ``Initialization`` byte range from a SegmentBase node.

    Bilibili returns ``Initialization`` either as a plain string (``"0-123"``)
    or as an object (``{"range": "0-123"}``). Handle both.
    """
    init = sb.get("Initialization") or sb.get("initialization")
    if isinstance(init, dict):
        return str(init.get("range") or init.get("indexRange") or "0-999")
    if isinstance(init, str) and init:
        return init
    return "0-999"


def _generate_vod_mpd_py(vid, idx, dash_data) -> str | None:
    """Generate an ``isoff-on-demand`` MPD for dash.js playback (Python fallback)."""
    if not dash_data or not isinstance(dash_data, dict):
        return None
    dash = dash_data.get("dash") or {}
    if not dash:
        return None

    duration = dash.get("duration", 0) or 0
    min_buffer = dash.get("minBufferTime", 1.5)
    period_dur = f' mediaPresentationDuration="PT{duration}S"' if duration else ""

    mpd = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static"{period_dur} minBufferTime="PT{min_buffer}S">',
        '  <Period id="1" start="PT0S">',
    ]

    # --- Video adaptation sets (one per codec) ---
    as_id = 1
    videos = _normalize_track_urls(dash.get("video", []))
    if videos:
        from collections import OrderedDict

        codec_groups: OrderedDict[int, list] = OrderedDict()
        for video_track in videos:
            codecid = video_track.get("codecid", 0)
            codec_groups.setdefault(codecid, []).append(video_track)
        as_id = 1
        for _codecid, tracks in codec_groups.items():
            codecs_str = tracks[0].get("codecs") or "avc1.64001F"
            mpd.append(
                f'    <AdaptationSet id="{as_id}" contentType="video" mimeType="video/mp4"'
                f' codecs="{codecs_str}" segmentAlignment="true"'
                f' subsegmentAlignment="true" startWithSAP="1">'
            )
            for video_track in tracks:
                qn = video_track.get("id")
                cid = video_track.get("codecid", 0)
                bandwidth = video_track.get("bandwidth", 0)
                width = video_track.get("width", 0)
                height = video_track.get("height", 0)
                frame_rate = video_track.get("frameRate") or video_track.get("frame_rate") or "24"
                if qn is None or not video_track.get("SegmentBase"):
                    continue
                sb = video_track["SegmentBase"]
                init_range = _sb_initialization_range(sb)
                index_range = sb.get("indexRange", "")
                index_exact = ' indexRangeExact="true"' if index_range else ""
                if not index_range:
                    continue
                track_codecs = video_track.get("codecs") or codecs_str
                mpd.append(
                    f'      <Representation id="video_{qn}_{cid}" codecs="{track_codecs}" bandwidth="{bandwidth}" width="{width}" height="{height}" frameRate="{frame_rate}">'
                )
                mpd.append(f"        <BaseURL>/proxy/dash/{vid}/{idx}/video/{qn}/{cid}</BaseURL>")
                mpd.append(f'        <SegmentBase indexRange="{index_range}"{index_exact}>')
                mpd.append(f'          <Initialization range="{init_range}"/>')
                mpd.append("        </SegmentBase>")
                mpd.append("      </Representation>")
            mpd.append("    </AdaptationSet>")
            as_id += 1

    # --- Audio adaptation set ---
    audios = _normalize_track_urls(dash.get("audio", []))
    if not audios:
        audios = _normalize_track_urls((dash.get("dolby") or {}).get("audio", []))
    if not audios:
        audios = _normalize_track_urls((dash.get("flac") or {}).get("audio", []))
    if audios:
        mpd.append(
            f'    <AdaptationSet id="{as_id}" contentType="audio" mimeType="audio/mp4" segmentAlignment="true" subsegmentAlignment="true" startWithSAP="1">'
        )
        for audio_track in audios:
            qn = audio_track.get("id")
            cid = audio_track.get("codecid", 0)
            bandwidth = audio_track.get("bandwidth", 0)
            codecs = audio_track.get("codecs") or "mp4a.40.2"
            if qn is None or not audio_track.get("SegmentBase"):
                continue
            sb = audio_track["SegmentBase"]
            init_range = _sb_initialization_range(sb)
            index_range = sb.get("indexRange", "")
            index_exact = ' indexRangeExact="true"' if index_range else ""
            if not index_range:
                continue
            mpd.append(f'      <Representation id="audio_{qn}_{cid}" codecs="{codecs}" bandwidth="{bandwidth}">')
            mpd.append(f"        <BaseURL>/proxy/dash/{vid}/{idx}/audio/{qn}/{cid}</BaseURL>")
            mpd.append(f'        <SegmentBase indexRange="{index_range}"{index_exact}>')
            mpd.append(f'          <Initialization range="{init_range}"/>')
            mpd.append("        </SegmentBase>")
            mpd.append("      </Representation>")
        mpd.append("    </AdaptationSet>")

    mpd.append("  </Period>")
    mpd.append("</MPD>")
    return "\n".join(mpd)


def generate_vod_mpd(vid, idx, dash_data) -> str | None:
    """Generate an ``isoff-on-demand`` MPD for dash.js playback.

    Prioritizes the native Rust implementation (miku_core_rs) when available;
    falls back cleanly to the pure-Python implementation on failure or absence.
    """
    if rs_generate_vod_mpd is not None and isinstance(dash_data, dict):
        try:
            mpd = rs_generate_vod_mpd(str(vid), int(idx), dash_data)
            if mpd is not None:
                return mpd
        except Exception:
            pass
    return _generate_vod_mpd_py(vid, idx, dash_data)


# ---------------------------------------------------------------------------


@app.route("/video/dash/<vid>/<int:idx>/manifest.mpd")
@rate_limit(**RATE_LIMITS["proxy"])
async def video_dash_manifest_view(vid, idx):
    """Serve the isoff-on-demand MPD manifest for a video part.

    ``?fresh=1`` drops the cached playurl first so the manifest is built
    from freshly re-fetched CDN URLs — the player recovery path for expired
    signatures (backend half of the official player's ``prefetchPlayUrl``).
    """
    if request.args.get("fresh") == "1":
        try:
            await appredis.delete(f"miku_dash_{vid}_{idx}")
            await appredis.delete(f"miku_dash_{vid}_{idx}:miss")
        except Exception:
            pass
    dash_data = await _load_dash_data(vid, idx)
    mpd_content = generate_vod_mpd(vid, idx, dash_data) if dash_data else None
    if not mpd_content:
        return Response("Not Found", status=404)
    return Response(mpd_content, content_type="application/dash+xml")
