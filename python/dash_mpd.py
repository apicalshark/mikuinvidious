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


import time

from dash_common import _is_valid_vid
from dash_fetch import _load_dash_data, _normalize_track_urls, _refresh_dash_data
from quart import Response, request
from rate_limit import RATE_LIMITS, ip_family_key, rate_limit
from shared import app


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


def generate_vod_mpd(vid, idx, dash_data) -> str | None:
    """Generate an ``isoff-on-demand`` MPD for dash.js playback."""
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
        audios = sorted(
            audios,
            key=lambda a: int(a.get("bandwidth", 0) or 0) if isinstance(a, dict) else 0,
            reverse=True,
        )
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


_generate_vod_mpd_py = generate_vod_mpd


# ---------------------------------------------------------------------------


# Cooldown for ``?fresh=1`` manifest cache-busting per (vid, idx) so a crawler
# or abusive client cannot force an upstream Bilibili WBI playurl fetch on
# every request. Legitimate player recovery fires at most once per 10 minutes.
_DASH_MANIFEST_FRESH_COOLDOWN = 30.0
_MANIFEST_FRESH_MAX = 1024
_MANIFEST_FAILURE_COOLDOWN = 5.0
_manifest_fresh_at: dict[str, float] = {}
_manifest_failed_at: dict[str, float] = {}
_manifest_refreshing: set[str] = set()
_manifest_new_refreshing: set[str] = set()
_manifest_next_sweep = 0.0


@app.route("/video/dash/<vid>/<int:idx>/manifest.mpd")
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("dash_manifest"))
async def video_dash_manifest_view(vid, idx):
    """Serve the isoff-on-demand MPD manifest for a video part.

    ``?fresh=1`` drops the cached playurl first so the manifest is built
    from freshly re-fetched CDN URLs — the player recovery path for expired
    signatures (backend half of the official player's ``prefetchPlayUrl``).
    Throttled per video by ``_DASH_MANIFEST_FRESH_COOLDOWN`` so repeated
    ``?fresh=1`` hits cannot stampede the upstream playurl API.
    """
    if not _is_valid_vid(vid) or idx < 0:
        return Response("Bad Request", status=400)
    dash_data = None
    if request.args.get("fresh") == "1":
        key = f"{vid}_{idx}"
        now = time.monotonic()
        global _manifest_next_sweep
        last_fresh = _manifest_fresh_at.get(key)
        last_failure = _manifest_failed_at.get(key)
        eligible = (
            key not in _manifest_refreshing
            and (last_fresh is None or now - last_fresh >= _DASH_MANIFEST_FRESH_COOLDOWN)
            and (last_failure is None or now - last_failure >= _MANIFEST_FAILURE_COOLDOWN)
        )
        if eligible:
            known = key in _manifest_fresh_at or key in _manifest_failed_at
            occupied = len(_manifest_fresh_at) + len(_manifest_failed_at) + len(_manifest_new_refreshing)
            if not known and occupied >= _MANIFEST_FRESH_MAX and now >= _manifest_next_sweep:
                for k, ts in list(_manifest_fresh_at.items()):
                    if now - ts >= _DASH_MANIFEST_FRESH_COOLDOWN and k not in _manifest_refreshing:
                        del _manifest_fresh_at[k]
                for k, ts in list(_manifest_failed_at.items()):
                    if now - ts >= _MANIFEST_FAILURE_COOLDOWN and k not in _manifest_refreshing:
                        del _manifest_failed_at[k]
                _manifest_next_sweep = now + _MANIFEST_FAILURE_COOLDOWN
                occupied = len(_manifest_fresh_at) + len(_manifest_failed_at) + len(_manifest_new_refreshing)
            if known or occupied < _MANIFEST_FRESH_MAX:
                _manifest_refreshing.add(key)
                if not known:
                    _manifest_new_refreshing.add(key)
                try:
                    dash_data = await _refresh_dash_data(vid, idx)
                    if dash_data:
                        _manifest_fresh_at[key] = time.monotonic()
                        _manifest_failed_at.pop(key, None)
                    else:
                        _manifest_fresh_at.pop(key, None)
                        _manifest_failed_at[key] = time.monotonic()
                finally:
                    _manifest_refreshing.discard(key)
                    _manifest_new_refreshing.discard(key)
    if dash_data is None:
        dash_data = await _load_dash_data(vid, idx)
    mpd_content = generate_vod_mpd(vid, idx, dash_data) if dash_data else None
    if not mpd_content:
        return Response("Not Found", status=404)
    return Response(mpd_content, content_type="application/dash+xml")
