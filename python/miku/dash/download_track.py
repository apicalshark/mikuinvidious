# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import re
import shutil
import time
from urllib.parse import urlparse

from miku.dash.common import (
    _CUT_ERRORS,
    _DOWNLOAD_CANCELLED,
    _DOWNLOAD_GOOD_BPS,
    _FREE_DOWNLOAD_MAX_QN,
    _RETRYABLE_TRACK_ERRORS,
    _SLOW_ARM_BPS,
    _SLOW_TRIP_PAUSE,
    _SLOW_WINDOW,
    _SLOW_WINDOW_KBPS,
    _SLOW_WINDOW_STRIKES,
    _TRACK_DOWNLOAD_RETRY_DELAYS,
    _JobCancelled,
    _max_download_track_bytes,
    _note_mirror_bad,
    _note_mirror_ok,
    _pace_delay,
    _parse_content_range_full,
    _parse_content_range_start,
    _segment_mirror_order,
    _TrackCut,
)
from miku.dash.fetch import _normalize_track_urls, _playurl_url_expiry
from miku.dash.stream import (
    _dash_candidate_urls,
    _DashUpstreamError,
    _failover_dash_conn,
    _ResumedDashConn,
    _SlowDashBody,
    _yield_dash_body,
)
from miku.media.stream import CdnConnectError, CdnConnection, CdnProtocolError, CdnTimeoutError
from miku.state import TicketManager

_DOWNLOAD_SEGMENT_SIZE = 32 * 1024 * 1024
_DOWNLOAD_WRITE_BATCH = 1024 * 1024


# Sentinel returned by _download_track_to_file when a job cancel was observed.
# Resume-with-backoff when the CDN/WARP tunnel cuts a track download mid-body
# ("Upstream connection closed prematurely", connection resets, read timeouts).
# Retries are UNBOUNDED (a download never gives up unless the user cancels);
# backoff delays plateau at the last entry (16s) so a troubled download waits
# patiently instead of hammering.


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except TypeError, ValueError:
        return default


_VIDEO_CODEC_RANK = {13: 3, 12: 2, 7: 1}
_QUALITY_NOISE_RE = re.compile(
    r"\s*(?:超高清|超清|高清|清晰|流畅|流暢|标清|標清|极速|極速|高码率|高碼率|高帧率|高幀率)"
)


def _clean_quality_desc(desc: str) -> str:
    cleaned = _QUALITY_NOISE_RE.sub("", str(desc or ""))
    return re.sub(r"(\d+)P\b", r"\1p", cleaned).strip()


def _video_codec_label(codecid: int, codecs: str) -> str:
    low = (codecs or "").lower()
    if "av01" in low or codecid == 13:
        return "AV1"
    if "hev" in low or "hvc" in low or codecid == 12:
        return "HEVC"
    if "avc" in low or codecid == 7:
        return "AVC"
    return codecs.split(".")[0].upper() if codecs else "VIDEO"


def _audio_codec_label(qn: int, codecs: str) -> tuple[str, str]:
    low = (codecs or "").lower()
    if "flac" in low or qn == 30251:
        return "FLAC", "flac"
    if "ec-3" in low or "ac-3" in low or qn == 30250:
        return "Dolby", "m4a"
    if "40.5" in low:
        return "HE-AAC", "m4a"
    return "AAC", "m4a"


def _all_dash_audio_tracks(dash: dict) -> list[dict]:
    seen: set[tuple[int, int]] = set()
    tracks: list[dict] = []
    sources = (
        dash.get("audio"),
        (dash.get("dolby") or {}).get("audio"),
        (dash.get("flac") or {}).get("audio"),
    )
    for src in sources:
        for t in _normalize_track_urls(src):
            qn = _safe_int(t.get("id"))
            cid = _safe_int(t.get("codecid"))
            if qn <= 0 or not _dash_candidate_urls(t):
                continue
            key = (qn, cid)
            if key in seen:
                continue
            seen.add(key)
            tracks.append(t)
    return tracks


def extract_download_options(dash_data: dict | None, *, max_video_qn: int = _FREE_DOWNLOAD_MAX_QN) -> dict:
    """Build TypeType-style video and audio format lists from DASH playurl data."""
    if not dash_data or not isinstance(dash_data, dict):
        return {"videos": [], "audios": [], "progressive": []}
    dash = dash_data.get("dash") or {}
    if not isinstance(dash, dict):
        return {"videos": [], "audios": [], "progressive": []}

    duration = _safe_int(dash.get("duration"))
    if duration <= 0:
        duration = _safe_int(dash_data.get("timelength")) // 1000

    fmt_map: dict[int, str] = {}
    for fmt in dash_data.get("support_formats") or []:
        if not isinstance(fmt, dict):
            continue
        fqn = _safe_int(fmt.get("quality"))
        desc = fmt.get("new_description") or fmt.get("display_desc") or ""
        if fqn > 0 and desc:
            fmt_map[fqn] = str(desc)

    videos: list[dict] = []
    seen_videos: set[tuple[int, int]] = set()
    for t in _normalize_track_urls(dash.get("video")):
        qn = _safe_int(t.get("id"))
        cid = _safe_int(t.get("codecid"))
        if qn <= 0 or qn > max_video_qn or not _dash_candidate_urls(t):
            continue
        if (qn, cid) in seen_videos:
            continue
        seen_videos.add((qn, cid))
        bw = max(_safe_int(t.get("bandwidth")), 0)
        width = max(_safe_int(t.get("width")), 0)
        height = max(_safe_int(t.get("height")), 0)
        fps_raw = t.get("frameRate") or t.get("frame_rate") or 0
        try:
            fps = round(float(fps_raw))
        except TypeError, ValueError:
            fps = 0
        codecs = str(t.get("codecs") or "")
        size_bytes = int(bw * duration / 8) if bw > 0 and duration > 0 else 0
        if height >= 4320:
            desc = "8K"
        elif height >= 2160:
            desc = "4K"
        elif height > 0:
            desc = f"{height}p"
        else:
            desc = _clean_quality_desc(fmt_map.get(qn, "")) or f"qn {qn}"
        videos.append(
            {
                "id": qn,
                "codecid": cid,
                "codecs": codecs,
                "codec_label": _video_codec_label(cid, codecs),
                "width": width,
                "height": height,
                "fps": fps,
                "bandwidth": bw,
                "description": desc,
                "container": "mp4",
                "size_bytes": size_bytes,
            }
        )
    videos.sort(
        key=lambda item: (
            item["height"],
            item["id"],
            item["fps"],
            _VIDEO_CODEC_RANK.get(item["codecid"], 0),
            item["bandwidth"],
        ),
        reverse=True,
    )

    audios: list[dict] = []
    for t in _all_dash_audio_tracks(dash):
        qn = _safe_int(t.get("id"))
        cid = _safe_int(t.get("codecid"))
        bw = max(_safe_int(t.get("bandwidth")), 0)
        codecs = str(t.get("codecs") or "mp4a.40.2")
        codec_label, container = _audio_codec_label(qn, codecs)
        size_bytes = int(bw * duration / 8) if bw > 0 and duration > 0 else 0
        audios.append(
            {
                "id": qn,
                "codecid": cid,
                "codecs": codecs,
                "codec_label": codec_label,
                "bandwidth": bw,
                "container": container,
                "size_bytes": size_bytes,
            }
        )
    audios.sort(key=lambda item: item["bandwidth"], reverse=True)
    return {"videos": videos, "audios": audios, "progressive": []}


def _pick_audio_track(
    dash_data: dict | None,
    *,
    audio_qn: int | None = None,
    audio_cid: int | None = None,
) -> dict | None:
    """Pick the requested DASH audio track, or the highest-bandwidth standard track."""
    if not dash_data or not isinstance(dash_data, dict):
        return None
    dash = dash_data.get("dash") or {}
    all_tracks = _all_dash_audio_tracks(dash)
    if audio_qn is not None and audio_qn > 0:
        if audio_cid is not None:
            for t in all_tracks:
                if _safe_int(t.get("id")) == audio_qn and _safe_int(t.get("codecid")) == audio_cid:
                    return t
        for t in all_tracks:
            if _safe_int(t.get("id")) == audio_qn:
                return t

    standard = _normalize_track_urls(dash.get("audio"))
    candidates = [t for t in standard if _dash_candidate_urls(t)] or all_tracks
    if not candidates:
        return None
    return max(candidates, key=lambda t: max(_safe_int(t.get("bandwidth")), 0))


def _pick_download_tracks(
    dash_data: dict | None,
    max_video_qn: int,
    *,
    video_cid: int | None = None,
    audio_qn: int | None = None,
    audio_cid: int | None = None,
) -> tuple:
    """Pick the best (video, audio) track pair for a muxed download.

    Video: exact ``(max_video_qn, video_cid)`` when requested, falling back to
    highest ``id`` (quality) <= ``max_video_qn``. Audio: requested track or
    highest ``bandwidth`` from the standard ``dash.audio`` array.
    """
    if not dash_data or not isinstance(dash_data, dict):
        return None, None
    dash = dash_data.get("dash") or {}

    videos = _normalize_track_urls(dash.get("video"))
    video = None
    if video_cid is not None:
        for t in videos:
            if _safe_int(t.get("id")) == max_video_qn and _safe_int(t.get("codecid")) == video_cid:
                video = t
                break
    if video is None:
        for t in videos:
            qn = _safe_int(t.get("id"))
            cid = _safe_int(t.get("codecid"))
            if qn <= max_video_qn:
                if video is None:
                    video = t
                    continue
                best_qn = _safe_int(video.get("id"))
                best_cid = _safe_int(video.get("codecid"))
                if qn > best_qn:
                    video = t
                elif qn == best_qn:
                    if video_cid is not None:
                        if cid == video_cid and best_cid != video_cid:
                            video = t
                    elif _VIDEO_CODEC_RANK.get(cid, 0) > _VIDEO_CODEC_RANK.get(best_cid, 0):
                        video = t

    audio = _pick_audio_track(dash_data, audio_qn=audio_qn, audio_cid=audio_cid)
    return video, audio


async def _open_cdn_track(url: str, headers: dict, proxy_url: str):
    """Connect + send request + read headers, with one bili_ticket refresh retry.

    Returns ``(conn, resp_headers)``; the caller owns ``conn.close()``.
    """
    headers = dict(headers)
    conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
    try:
        await conn.connect()
        await conn.send_request()
        resp_headers = await conn.read_response_headers()
    except BaseException:
        await conn.close()
        raise
    if resp_headers.status_code in (403, 412, 514):
        await conn.close()
        ticket = await TicketManager.get_ticket(force_refresh=True)
        if ticket:
            headers["x-bili-ticket"] = ticket
        else:
            headers.pop("x-bili-ticket", None)
        conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
        try:
            await conn.connect()
            await conn.send_request()
            resp_headers = await conn.read_response_headers()
        except BaseException:
            await conn.close()
            raise
    return conn, resp_headers


async def _await_track_retry(
    attempt: int, cancel_event: asyncio.Event | None, note_cb, exc: Exception, label: str | None = None
) -> str:
    """Back off before resume attempt ``attempt`` (0-based). Returns 'retry' or 'cancelled'.

    Never 'abort's on its own: downloads persist until the user cancels.
    Backoff plateaus at the last entry of ``_TRACK_DOWNLOAD_RETRY_DELAYS``.
    """
    where = f" {label}" if label else ""
    print(f"[DashProxy] track download{where} cut, will resume (retry #{attempt + 1}): {exc}")
    delay = _TRACK_DOWNLOAD_RETRY_DELAYS[min(attempt, len(_TRACK_DOWNLOAD_RETRY_DELAYS) - 1)]
    if note_cb is not None:
        note_cb(f"Connection interrupted, retrying in {delay:g}s (attempt #{attempt + 1})…")
    if cancel_event is None:
        await asyncio.sleep(delay)
        return "retry"
    try:
        async with asyncio.timeout(delay):
            await cancel_event.wait()
        return "cancelled"
    except TimeoutError:
        return "retry"


async def _download_track_to_file(
    url: str | list,
    headers: dict,
    proxy_url: str,
    dest: str,
    max_bytes: int | None = None,
    progress_cb=None,
    cancel_event: asyncio.Event | None = None,
    note_cb=None,
    rewind_cb=None,
) -> int:
    """Download a full track body to ``dest`` via CdnConnection, resuming on cuts.

    ``url`` may be a single URL or a candidate mirror list (primary +
    backups, see :func:`_dash_candidate_urls`). Each resume retry rotates to
    the next mirror: individual CDN objects stall/cut on one edge while
    siblings serve fine, and hammering the same sick edge is what used to
    fail jobs after a couple of cuts.

    When upstream cuts the connection mid-body (reset / premature close /
    read timeout) or answers a resume with a transient status / offset
    mismatch, waits a few seconds and resumes from the downloaded offset
    with a ``Range`` request, retrying without limit until the user cancels
    (backoff plateaus at 16s between attempts).

    Returns byte count, ``-1`` on error/oversize, or ``_DOWNLOAD_CANCELLED``
    when ``cancel_event`` is set (checked per chunk and during backoff waits;
    the partial file is left for caller cleanup).

    ``max_bytes`` defaults to the hoster-configured per-track cap.
    """
    if isinstance(url, (list, tuple)):
        candidates = [u for u in url if isinstance(u, str) and u]
    else:
        candidates = [url] if isinstance(url, str) and url else []
    if not candidates:
        print("[DashProxy] track download error: no candidate URLs")
        return -1
    if max_bytes is None:
        max_bytes = _max_download_track_bytes()
    total = 0
    attempt = 0
    mirror_idx = 0
    file_obj = None
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return _DOWNLOAD_CANCELLED
            current_url = candidates[mirror_idx % len(candidates)]
            try:
                total, file_obj, outcome = await _fetch_track_attempt(
                    current_url,
                    headers,
                    proxy_url,
                    dest,
                    file_obj,
                    total,
                    max_bytes,
                    progress_cb,
                    cancel_event,
                    rewind_cb,
                )
            except _CUT_ERRORS as exc:
                if isinstance(exc, _TrackCut):
                    # Keep the bytes streamed before the cut so we resume, not restart.
                    total, file_obj = exc.total, exc.file_obj
                    cause = exc.cause
                else:
                    # Cut during connect/headers: no progress made yet.
                    cause = exc
                decision = await _await_track_retry(attempt, cancel_event, note_cb, cause)
                if decision == "retry":
                    attempt += 1
                    # Rotate mirrors so the resume hits a different edge.
                    mirror_idx += 1
                    continue
                return _DOWNLOAD_CANCELLED if decision == "cancelled" else -1
            if note_cb is not None:
                note_cb(None)
            if outcome == "cancelled":
                return _DOWNLOAD_CANCELLED
            return total if outcome == "done" else -1
    except Exception as exc:
        print(f"[DashProxy] track download error: {exc}")
        return -1
    finally:
        if file_obj is not None:
            await asyncio.to_thread(file_obj.close)


def _validate_track_response(resp_headers, total: int, max_bytes: int) -> str:
    """Classify a track GET response: 'ok', 'restart', 'retry', or 'fatal'.

    'restart' means the server ignored our resume ``Range`` (HTTP 200) — the
    caller must truncate and start over. 'retry' (transient HTTP status,
    resume offset mismatch) must be retried, preferably on the next mirror.
    'fatal' (oversize) must not be retried.
    """
    status = resp_headers.status_code
    if status not in (200, 206):
        print(
            f"[DashProxy] track GET -> HTTP {status} "
            f"(have {total} bytes, content-range={resp_headers.headers.get('content-range')}), will retry on next mirror"
        )
        return "retry"
    if total > 0:
        if status != 206:
            return "restart"
        if _parse_content_range_start(resp_headers.headers) != total:
            print(
                f"[DashProxy] resume offset mismatch (have {total}, server {resp_headers.headers.get('content-range')}), "
                "will retry on next mirror"
            )
            return "retry"
        return "ok"
    cl_header = resp_headers.headers.get("content-length")
    if cl_header:
        try:
            if int(cl_header) > max_bytes:
                print(f"[DashProxy] track Content-Length {cl_header} exceeds limit {max_bytes}")
                return "fatal"
        except ValueError:
            pass
    return "ok"


async def _fetch_track_attempt(
    url: str,
    headers: dict,
    proxy_url: str,
    dest: str,
    file_obj,
    total: int,
    max_bytes: int,
    progress_cb,
    cancel_event,
    rewind_cb,
):
    """One GET (or Range-resume) attempt. Returns ``(total, file_obj, outcome)``.

    ``outcome`` is 'done' (clean EOF), 'fatal' (do not retry), or 'cancelled'.
    Raises ``_TrackCut`` (carrying partial progress) or ``_RETRYABLE_TRACK_ERRORS``
    on connection cuts so the caller can back off and resume. A 'retry'
    validation (transient HTTP status / offset mismatch) raises
    ``CdnProtocolError`` for the same resume path.
    """
    req_headers = dict(headers)
    if total > 0:
        req_headers["Range"] = f"bytes={total}-"
    conn, resp_headers = await _open_cdn_track(url, req_headers, proxy_url)
    try:
        action = _validate_track_response(resp_headers, total, max_bytes)
        if action == "retry":
            raise CdnProtocolError(
                f"CDN returned HTTP {resp_headers.status_code} for Range resume (have {total} bytes)"
            )
        if action == "fatal":
            print(
                f"[DashProxy] track GET fatal: HTTP {resp_headers.status_code} "
                f"(have {total} bytes, content-range={resp_headers.headers.get('content-range')}, "
                f"content-length={resp_headers.headers.get('content-length')})"
            )
            return total, file_obj, "fatal"
        if action == "restart":
            if file_obj is not None:
                await asyncio.to_thread(file_obj.close)
                file_obj = None
            if rewind_cb is not None:
                rewind_cb(total)
            total = 0
        if file_obj is None:
            file_obj = await asyncio.to_thread(open, dest, "wb" if total == 0 else "ab")
        total, outcome = await _stream_track_body(conn, file_obj, total, max_bytes, progress_cb, cancel_event)
        return total, file_obj, outcome
    finally:
        await conn.close()


async def _stream_track_body(conn, file_obj, total: int, max_bytes: int, progress_cb, cancel_event):
    """Stream ``iter_chunks()`` to ``file_obj``. Returns ``(total, outcome)``.

    Raises ``_TrackCut`` (carrying the bytes streamed so far) on connection
    cuts so the caller can resume with ``Range`` instead of restarting.
    """
    try:
        async for chunk in conn.iter_chunks():
            if cancel_event is not None and cancel_event.is_set():
                return total, "cancelled"
            prospective_total = total + len(chunk)
            if prospective_total > max_bytes:
                print(f"[DashProxy] downloaded bytes {prospective_total} exceeded limit {max_bytes}")
                return total, "fatal"
            await asyncio.to_thread(file_obj.write, chunk)
            total = prospective_total
            if progress_cb is not None:
                progress_cb(len(chunk))
        return total, "done"
    except _RETRYABLE_TRACK_ERRORS as exc:
        raise _TrackCut(total, file_obj, exc) from exc


_MUX_TIMEOUT = 600.0


async def _run_ffmpeg_cmd(args: list[str], job=None) -> None:
    ffmpeg = await asyncio.to_thread(shutil.which, "ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available on this server")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if job is not None:
            job.ffmpeg_proc = proc
            comm_task = asyncio.ensure_future(proc.communicate())
            cancel_task = asyncio.ensure_future(job.cancel_event.wait())
            try:
                done, _ = await asyncio.wait({comm_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
                if comm_task not in done:
                    proc.kill()
                    await proc.wait()
                    raise _JobCancelled()
                _, stderr = await comm_task
            finally:
                for t in (comm_task, cancel_task):
                    if not t.done():
                        t.cancel()
                job.ffmpeg_proc = None
        else:
            try:
                async with asyncio.timeout(_MUX_TIMEOUT):
                    _, stderr = await proc.communicate()
            except TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                raise RuntimeError("ffmpeg mux timed out") from None
    except _JobCancelled, asyncio.CancelledError:
        try:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        except Exception:
            pass
        raise
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg mux failed: " + (stderr or b"").decode(errors="replace")[-600:])


async def _mux_tracks(video_path: str, audio_path: str, out_path: str, *, is_mkv: bool = False, job=None) -> None:
    """Remux video + audio tracks into MP4 or MKV with ffmpeg (-c copy)."""
    cmd = [
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
    ]
    if not is_mkv:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(out_path)
    await _run_ffmpeg_cmd(cmd, job=job)


async def _remux_video_track(video_path: str, out_path: str, job=None) -> None:
    """Remux a single DASH video track into a playable silent MP4."""
    await _run_ffmpeg_cmd(
        ["-y", "-i", video_path, "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", out_path],
        job=job,
    )


async def _remux_audio_track(audio_path: str, out_path: str, *, is_flac: bool = False, job=None) -> None:
    """Remux a single DASH audio track into a playable M4A or FLAC container."""
    cmd = ["-y", "-i", audio_path, "-map", "0:a:0", "-c", "copy"]
    if not is_flac:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(out_path)
    await _run_ffmpeg_cmd(cmd, job=job)


def _has_exact_content_length(resp_headers, span_left: int) -> bool:
    """True when a 206 response carries Content-Length equal to the wanted span.

    Length-framed exact bodies are the only ones safe to (a) stream to a
    known end — a close-delimited 206 on a reused keep-alive socket would hang
    forever waiting for a close that never comes — and (b) keep alive
    afterwards, since exact consumption leaves the socket precisely at the
    next message boundary.
    """
    if resp_headers.status_code != 206:
        return False
    try:
        return int(str((resp_headers.headers or {}).get("content-length") or "").strip()) == span_left
    except TypeError, ValueError:
        return False


async def _download_track_segmented(
    urls,
    headers: dict,
    proxy_url: str,
    dest: str,
    total_size: int,
    label: str,
    max_bytes: int | None = None,
    progress_cb=None,
    cancel_event: asyncio.Event | None = None,
    note_cb=None,
    refresh_cb=None,
    max_refreshes: int | None = None,
) -> int:
    """Download a track as sequential player-sized Range segments to ``dest``.

    Same per-request behaviour as ``proxy_dash`` (primary-first mirrors,
    ``DASH_ATTEMPT_TIMEOUT`` handshakes, bili-ticket refresh, computable-length
    gate, ``DASH_BODY_GRACE``/``DASH_BODY_FLOOR_KBPS`` watchdog, byte-exact
    resume across mirrors), but appended to a file: mid-segment cuts and
    slow-loris bodies fail over to the next mirror for the *remainder* of the
    segment, and a fully exhausted mirror set backs off
    (``_await_track_retry``) and retries the segment from the next mirror,
    without limit until the user cancels (backoff plateaus at 16s).

    ``refresh_cb`` (``async () -> list | None``) fetches a fresh playurl and
    returns replacement mirror URLs for the *same* track when the current
    set is exhausted — all mirrors of one response share the expiry window,
    so rotating them cannot fix dead signatures. Refresh fires only for dead
    signatures (403-class sweep, or a provably-past URL stamp), never for
    mere slowness; ``max_refreshes=None`` (default) means unbounded, and a
    refresh returning the same URL stamp is always declined (the official
    player's same-stamp dedup): retrying identical signatures cannot succeed.

    Returns the byte count, ``-1`` on error/oversize, or
    ``_DOWNLOAD_CANCELLED`` when ``cancel_event`` is set.
    """
    if isinstance(urls, (list, tuple)):
        candidates = [u for u in urls if isinstance(u, str) and u]
    else:
        candidates = [urls] if isinstance(urls, str) and urls else []
    if not candidates:
        print(f"[DashProxy] {label} segmented download error: no candidate URLs")
        return -1
    if max_bytes is None:
        max_bytes = _max_download_track_bytes()
    if total_size > max_bytes:
        print(f"[DashProxy] {label} size {total_size} exceeds limit {max_bytes}")
        return -1
    file_obj = None
    written = 0
    seg_index = 0
    refreshes_used = 0
    last_stamp = _playurl_url_expiry(candidates[0])
    pending_writes: list = []
    pending_len = 0
    keepalive = None  # (conn, url): fully-consumed length-framed connection, reusable
    track_best_bps = 0.0  # best completed-segment (or early-window) rate this track
    slow_streak = 0  # consecutive slow trips: indexes _DOWNLOAD_PACE_DELAYS
    try:
        file_obj = await asyncio.to_thread(open, dest, "wb")
        while written < total_size:
            if cancel_event is not None and cancel_event.is_set():
                return _DOWNLOAD_CANCELLED
            seg_start = written
            seg_end = min(written + _DOWNLOAD_SEGMENT_SIZE - 1, total_size - 1)
            span = seg_end - seg_start + 1
            seg_label = f"{label} seg{seg_index}[{seg_start}-{seg_end}]"
            orig_range = f"bytes={seg_start}-{seg_end}"
            seg_headers = dict(headers)
            seg_headers["range"] = orig_range
            # Per-segment mirror order (rotation + sick-mirror memory, M-CDN
            # last): unlike the player (fragments spaced seconds apart), this
            # loop fires back-to-back at full speed — hammering one edge
            # invites throttling that reads as decaying speed.
            order = _segment_mirror_order(candidates, seg_index)
            start_idx = 0
            pending = order[:]
            seg_done = 0
            seg_attempt = 0
            seg_host = "-"
            seg_t0 = time.monotonic()
            win_t0 = seg_t0
            win_bytes = 0
            win_best = 0.0
            slow_strikes = 0
            slow_trips = 0
            seg_clean = True
            while seg_done < span:
                if cancel_event is not None and cancel_event.is_set():
                    return _DOWNLOAD_CANCELLED
                if pending_len:
                    # Disk must catch up to seg_done before any (re)fetch:
                    # resume ranges are computed from seg_done, so buffered
                    # bytes must be on disk first (abort paths skip this —
                    # their partial files are deleted by the caller).
                    batch = b"".join(pending_writes)
                    pending_writes.clear()
                    pending_len = 0
                    await asyncio.to_thread(file_obj.write, batch)
                conn = used = resp_headers = None
                if keepalive is not None:
                    # Kept-alive connection from the previous segment: no
                    # handshake, no slow-start restart. Only banked after an
                    # exactly-consumed length-framed body, so the socket is
                    # precisely at the next boundary. Any trouble falls back
                    # to full mirror failover below.
                    ka_conn, ka_url = keepalive
                    keepalive = None
                    try:
                        resp_headers = await ka_conn.next_request(seg_headers)
                        conn, used = ka_conn, ka_url
                    except asyncio.CancelledError:
                        try:
                            await ka_conn.close()
                        except Exception:
                            pass
                        raise
                    except (CdnConnectError, CdnProtocolError, CdnTimeoutError, OSError, TimeoutError) as exc:
                        try:
                            await ka_conn.close()
                        except Exception:
                            pass
                        print(f"[DashProxy] {seg_label} keep-alive reuse failed ({exc}), failing over")
                if conn is None:
                    try:
                        conn, used, resp_headers = await _failover_dash_conn(
                            pending, seg_headers, proxy_url, seg_label, orig_range, seg_done, span
                        )
                    except _DashUpstreamError as exc:
                        # Refresh means dead signatures, never "edge is slow": only
                        # when mirrors actively reject auth (403-class sweep) or
                        # the stamp is provably past. A tarpit exhaustion must
                        # rotate/backoff instead — new signatures on the same sick
                        # edge change nothing.
                        provably_expired = last_stamp is not None and time.time() > last_stamp
                        if (
                            refresh_cb is not None
                            and (max_refreshes is None or refreshes_used < max_refreshes)
                            and (exc.forbidden or provably_expired)
                        ):
                            if note_cb is not None:
                                note_cb("Video links expired, refreshing…")
                            try:
                                new_urls = await refresh_cb()
                            except Exception as refresh_exc:
                                print(f"[DashProxy] {seg_label} playurl refresh failed: {refresh_exc}")
                                new_urls = None
                            if new_urls:
                                new_stamp = _playurl_url_expiry(new_urls[0])
                                if new_stamp != last_stamp:
                                    print(
                                        f"[DashProxy] {seg_label} refreshed playurl "
                                        f"(stamp {last_stamp} -> {new_stamp}), resuming"
                                    )
                                    candidates = new_urls
                                    last_stamp = new_stamp
                                    refreshes_used += 1
                                    seg_attempt = 0
                                    slow_strikes = 0
                                    slow_trips = 0
                                    order = _segment_mirror_order(candidates, seg_index)
                                    start_idx = 0
                                    pending = order[:]
                                    if note_cb is not None:
                                        note_cb(None)
                                    continue
                                print(f"[DashProxy] {seg_label} refresh returned same URL stamp, not retrying refresh")
                        decision = await _await_track_retry(seg_attempt, cancel_event, note_cb, exc, label=seg_label)
                        if decision == "retry":
                            seg_attempt += 1
                            start_idx = (start_idx + 1) % len(order)
                            pending = order[start_idx:] + order[:start_idx]
                            continue
                        return _DOWNLOAD_CANCELLED if decision == "cancelled" else -1
                seg_host = urlparse(used).hostname or "-"
                slow_strikes = 0  # fresh mirror: benefit of the doubt for its first windows
                exact_206 = False
                # Validate the mirror answered at the wanted offset. A
                # _ResumedDashConn from _failover_dash_conn is already
                # byte-contiguous (prefix skipped, remainder capped); checking
                # it again would wrap it twice and _yield_dash_body unwraps one
                # level only, skipping the prefix twice. This covers the
                # initial fetch, where an edge may ignore the Range (200) or
                # answer off-offset.
                want = seg_start + seg_done
                span_left = span - seg_done
                use_conn = conn
                status = resp_headers.status_code
                if isinstance(conn, _ResumedDashConn):
                    pass
                elif status == 206:
                    rs, re, _ = _parse_content_range_full(resp_headers.headers.get("content-range"))
                    if rs == want:
                        # Exact length framing is also what makes a response
                        # keep-alive-safe (see _has_exact_content_length).
                        if _has_exact_content_length(resp_headers, span_left):
                            exact_206 = True
                        else:
                            print(f"[DashProxy] {seg_label} 206 without exact length, trying next mirror")
                            try:
                                await conn.close()
                            except Exception:
                                pass
                            continue
                    elif rs is not None and re is not None and rs < want <= re:
                        print(f"[DashProxy] {seg_label} resume overlap, salvaging")
                        use_conn = _ResumedDashConn(conn, want - rs, span_left)
                    else:
                        print(
                            f"[DashProxy] {seg_label} offset mismatch "
                            f"(want {want}, got {resp_headers.headers.get('content-range')}), trying next mirror"
                        )
                        try:
                            await conn.close()
                        except Exception:
                            pass
                        continue
                elif status == 200:
                    try:
                        full_len = int(str(resp_headers.headers.get("content-length") or "").strip())
                    except TypeError, ValueError:
                        full_len = None
                    if full_len is not None and full_len > want:
                        print(f"[DashProxy] {seg_label} ignored Range, salvaging full body")
                        use_conn = _ResumedDashConn(conn, want, span_left)
                    else:
                        try:
                            await conn.close()
                        except Exception:
                            pass
                        continue
                else:
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    continue
                win_t0 = None
                win_bytes = 0
                stream_complete = False
                try:
                    try:
                        async for chunk in _yield_dash_body(use_conn, seg_label):
                            if cancel_event is not None and cancel_event.is_set():
                                return _DOWNLOAD_CANCELLED
                            if written + seg_done + len(chunk) > max_bytes:
                                print(f"[DashProxy] {seg_label} exceeds limit {max_bytes}")
                                return -1
                            pending_writes.append(chunk)
                            pending_len += len(chunk)
                            seg_done += len(chunk)
                            if progress_cb is not None:
                                progress_cb(len(chunk))
                            now_w = time.monotonic()
                            if win_t0 is None:
                                win_t0 = now_w
                            win_bytes += len(chunk)
                            if now_w - win_t0 >= _SLOW_WINDOW:
                                win_el = now_w - win_t0
                                if win_el > 0:
                                    win_rate = win_bytes / win_el
                                    if win_rate > win_best:
                                        win_best = win_rate
                                    capable = track_best_bps >= _SLOW_ARM_BPS or win_best >= _SLOW_ARM_BPS
                                    if win_rate >= _SLOW_WINDOW_KBPS * 1024:
                                        slow_strikes = 0
                                    elif capable:
                                        slow_strikes += 1
                                win_t0, win_bytes = now_w, 0
                                if slow_strikes >= _SLOW_WINDOW_STRIKES and slow_trips < len(order):
                                    slow_trips += 1
                                    raise _SlowDashBody(
                                        f"slow {win_rate / 1024:.0f} KB/s x{slow_strikes} windows "
                                        f"(< {_SLOW_WINDOW_KBPS} KB/s)"
                                    )
                            if pending_len >= _DOWNLOAD_WRITE_BATCH:
                                batch = b"".join(pending_writes)
                                pending_writes.clear()
                                pending_len = 0
                                await asyncio.to_thread(file_obj.write, batch)
                        stream_complete = True
                    finally:
                        if stream_complete and seg_done >= span and use_conn is conn and exact_206:
                            keepalive = (conn, used)
                        else:
                            try:
                                await use_conn.close()
                            except Exception:
                                pass
                except (
                    _SlowDashBody,
                    CdnConnectError,
                    CdnProtocolError,
                    CdnTimeoutError,
                    OSError,
                    TimeoutError,
                ) as exc:
                    print(f"[DashProxy] {seg_label} cut after {seg_done}/{span} bytes ({exc}), failing over")
                    _note_mirror_bad(seg_host)
                    seg_clean = False
                    slow = isinstance(exc, _SlowDashBody)
                    if slow:
                        # Slow (not dead): cut the upstream connection and wait
                        # a full _SLOW_TRIP_PAUSE before reconnecting — a fresh
                        # hammer lands back in the penalty box. slow_streak
                        # still steps (drives the inter-segment pacing ladder).
                        slow_streak += 1
                        pause = _SLOW_TRIP_PAUSE
                        if note_cb is not None:
                            note_cb(f"Too slow, pausing {pause:g}s before retry…")
                    else:
                        # Settle before reconnecting: hammering a shedding edge
                        # with instant reconnects earns empty closes ("Bad status
                        # line") and deepens the hole. Cancel-aware
                        # (user-cancel still aborts promptly).
                        pause = 1.0
                    if cancel_event is not None:
                        try:
                            async with asyncio.timeout(pause):
                                await cancel_event.wait()
                            return _DOWNLOAD_CANCELLED
                        except TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(pause)
                    continue
                if seg_done >= span:
                    break
                # Close-delimited body ended early with no error: resume the remainder.
                print(f"[DashProxy] {seg_label} short body ({seg_done}/{span} bytes), resuming remainder")
            if pending_len:
                batch = b"".join(pending_writes)
                pending_writes.clear()
                pending_len = 0
                await asyncio.to_thread(file_obj.write, batch)
            written += seg_done
            seg_el = time.monotonic() - seg_t0
            if seg_el > 0:
                track_best_bps = max(track_best_bps, seg_done / seg_el)
                if seg_clean and seg_attempt == 0 and slow_trips == 0 and seg_done / seg_el > _DOWNLOAD_GOOD_BPS:
                    slow_streak = 0
            _note_mirror_ok(seg_host)
            print(
                f"[DashProxy] {seg_label} done {seg_done}/{span} bytes in {seg_el:.1f}s "
                f"({seg_done / seg_el / 1024:.0f} KB/s) via {seg_host}"
            )
            seg_index += 1
            if note_cb is not None:
                note_cb(None)
            if written < total_size:
                # Pacing breather before the next segment (cancel-aware),
                # at the current ladder rung (escalated by slow trips).
                pause = _pace_delay(slow_streak)
                if cancel_event is not None:
                    try:
                        async with asyncio.timeout(pause):
                            await cancel_event.wait()
                        return _DOWNLOAD_CANCELLED
                    except TimeoutError:
                        pass
                else:
                    await asyncio.sleep(pause)
        if note_cb is not None:
            note_cb(None)
        return written
    except Exception as exc:
        print(f"[DashProxy] {label} segmented download error: {exc}")
        return -1
    finally:
        if keepalive is not None:
            try:
                await keepalive[0].close()
            except Exception:
                pass
            keepalive = None
        if file_obj is not None:
            await asyncio.to_thread(file_obj.close)


async def _download_track_file(
    urls,
    headers: dict,
    proxy_url: str,
    dest: str,
    total_size: int,
    label: str,
    max_bytes: int | None = None,
    progress_cb=None,
    cancel_event: asyncio.Event | None = None,
    note_cb=None,
    rewind_cb=None,
    refresh_cb=None,
    max_refreshes: int | None = None,
) -> int:
    """Download a track file, player-style when the size is known.

    With a known ``total_size`` (the normal case: callers peek it first) the
    track is fetched as sequential player-sized segments
    (:func:`_download_track_segmented`); when the size is unknown (peek
    failed) it falls back to the legacy whole-file resumable GET
    (:func:`_download_track_to_file`). Same return contract as both.
    """
    if total_size and total_size > 0:
        return await _download_track_segmented(
            urls,
            headers,
            proxy_url,
            dest,
            total_size,
            label,
            max_bytes=max_bytes,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            note_cb=note_cb,
            refresh_cb=refresh_cb,
            max_refreshes=max_refreshes,
        )
    return await _download_track_to_file(
        urls,
        headers,
        proxy_url,
        dest,
        max_bytes=max_bytes,
        progress_cb=progress_cb,
        cancel_event=cancel_event,
        note_cb=note_cb,
        rewind_cb=rewind_cb,
    )
