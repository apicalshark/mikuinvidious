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
import os
import shutil
import tempfile
import time
import uuid
from collections import deque

from csrf import csrf_protect
from dash_common import (
    _DOWNLOAD_CANCELLED,
    _FREE_DOWNLOAD_MAX_QN,
    DASH_FETCH_TIMEOUT,
    _build_dash_cdn_headers,
    _is_safe_dash_url,
    _is_safe_dash_url_async,
    _is_valid_vid,
    _JobCancelled,
    _max_download_size_mb,
    _max_download_track_bytes,
    _safe_download_filename,
    dash_proxy_bp,
)
from dash_download_track import (
    _download_track_file,
    _mux_tracks,
    _open_cdn_track,
    _pick_download_tracks,
)
from dash_fetch import (
    _DurlResolveError,
    _load_dash_data,
    _lookup_track,
    _refresh_dash_data,
    _resolve_durl_download,
    has_valid_dash_tracks,
    video_get_dash_for_qn,
)
from dash_stream import _dash_candidate_urls
from quart import Response, jsonify, redirect
from rate_limit import RATE_LIMITS, rate_limit
from shared import Network, appcred, appredis


async def _proxy_download_durl_fallback(vid: str, idx: int, qual: int):
    """Download fallback for durl-only videos (no playable DASH tracks).

    Progressive MP4s are already muxed (video + audio in one file), so no
    ffmpeg step is needed: resolve the best progressive URL at or below the
    requested quality and redirect to the native ``/proxy/video/`` path with
    ``?dl=1`` (attachment), reusing its ticket-refresh + backup-URL +
    lower-quality fallbacks.
    """
    from api import video as video_mod

    try:
        v = video_mod.Video(bvid=vid, credential=appcred)
    except Exception:
        return Response("Bad Request: invalid video ID", status=400)
    try:
        async with asyncio.timeout(8.0):
            play_data = await video_get_dash_for_qn(v, idx)
    except Exception as exc:
        print(f"[DashProxy] durl download fetch failed for {vid}:{idx}: {exc}")
        play_data = None
    try:
        url, qn, ext = await _resolve_durl_download(v, vid, idx, qual, play_data=play_data)
    except _DurlResolveError as exc:
        if exc.status == 502:
            return Response("Upstream error", status=502)
        return Response("Not Found", status=404)
    return redirect(f"/proxy/video/{vid}_{idx}_{qn}{ext}?dl=1", code=302)


@dash_proxy_bp.route("/proxy/download/<vid>/<int:idx>/<int:qual>")
@rate_limit(**RATE_LIMITS["proxy"])
async def proxy_download(vid, idx, qual):
    """Mux the best DASH video (<=1080p anonymous cap) + audio into one MP4.

    Downloads both tracks through the WARP tunnel, remuxes with ffmpeg into a
    single faststart MP4, and streams it back as an attachment.

    Durl-only uploads (no DASH tracks) fall back to a
    redirect at the native progressive ``/proxy/video/`` path — those MP4s
    are already muxed, so no ffmpeg step is needed.
    """
    if not _is_valid_vid(vid):
        return Response("Bad Request: invalid video ID", status=400)

    max_qn = min(qual, _FREE_DOWNLOAD_MAX_QN) if qual > 0 else _FREE_DOWNLOAD_MAX_QN
    dash_data = await _load_dash_data(vid, idx)
    if not has_valid_dash_tracks(dash_data):
        return await _proxy_download_durl_fallback(vid, idx, qual)

    video, audio = _pick_download_tracks(dash_data, max_qn)
    if not video or not audio:
        return Response("Not Found: no suitable DASH tracks", status=404)
    vurls = _dash_candidate_urls(video)
    aurls = _dash_candidate_urls(audio)
    if not vurls or not aurls:
        return Response("Not Found: track has no URL", status=404)
    v_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in vurls))
    a_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in aurls))
    vurls = [u for u, ok in zip(vurls, v_checks, strict=False) if ok]
    aurls = [u for u, ok in zip(aurls, a_checks, strict=False) if ok]
    if not vurls or not aurls:
        return Response("Forbidden: Invalid proxy target", status=403)

    proxy_url = Network.get_proxy()
    headers = await _build_dash_cdn_headers()

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix="miku_dl_")
    vpath = os.path.join(tmpdir, "video.m4s")
    apath = os.path.join(tmpdir, "audio.m4s")
    outpath = os.path.join(tmpdir, "out.mp4")
    response_owns_cleanup = False
    try:
        vsize = await _peek_content_length(vurls, headers, proxy_url)
        asize = await _peek_content_length(aurls, headers, proxy_url)
    except RuntimeError as exc:
        await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
        return Response(str(exc), status=413)

    try:
        v_qn = int(video.get("id") or 0)
    except TypeError, ValueError:
        v_qn = 0
    try:
        a_qn = int(audio.get("id") or 0)
    except TypeError, ValueError:
        a_qn = 0

    async def _refresh_legacy_track_urls(media_type: str, qn: int):
        fresh = await _refresh_dash_data(vid, idx)
        if not has_valid_dash_tracks(fresh):
            return None
        pick = _pick_download_tracks(fresh, max_qn)
        track = pick[0] if media_type == "video" else pick[1]
        if track is None:
            return None
        try:
            if int(track.get("id") or -1) != qn:
                return None
        except TypeError, ValueError:
            return None
        urls = _dash_candidate_urls(track)
        checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls))
        urls = [u for u, ok in zip(urls, checks, strict=False) if ok]
        return urls or None

    async def _refresh_legacy_video_urls():
        return await _refresh_legacy_track_urls("video", v_qn)

    async def _refresh_legacy_audio_urls():
        return await _refresh_legacy_track_urls("audio", a_qn)

    try:
        async with _download_limiter:
            vn, an = await asyncio.gather(
                _download_track_file(
                    vurls,
                    headers,
                    proxy_url,
                    vpath,
                    vsize,
                    f"{vid}:{idx} video",
                    refresh_cb=_refresh_legacy_video_urls,
                ),
                _download_track_file(
                    aurls,
                    headers,
                    proxy_url,
                    apath,
                    asize,
                    f"{vid}:{idx} audio",
                    refresh_cb=_refresh_legacy_audio_urls,
                ),
            )
            if vn < 0:
                return Response("Upstream error (video track)", status=502)
            if an < 0:
                return Response("Upstream error (audio track)", status=502)
            try:
                await _mux_tracks(vpath, apath, outpath)
            except RuntimeError as exc:
                print(f"[DashProxy] download mux failed for {vid}:{idx}: {exc}")
                return Response("Mux failed", status=502)
        actual_qn = int(video.get("id") or max_qn)

        async def generate():
            try:
                f = await asyncio.to_thread(open, outpath, "rb")
                try:
                    while True:
                        chunk = await asyncio.to_thread(f.read, 512 * 1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    await asyncio.to_thread(f.close)
            finally:
                await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)

        resp = Response(generate())
        resp.headers["Content-Type"] = "video/mp4"
        resp.headers["Content-Disposition"] = f'attachment; filename="{_safe_download_filename(vid, idx, actual_qn)}"'
        resp.headers["X-Accel-Buffering"] = "no"
        response_owns_cleanup = True
        return resp
    except Exception as exc:
        await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
        print(f"[DashProxy] proxy_download error: {exc}")
        return Response("Upstream error", status=502)
    finally:
        if not response_owns_cleanup:
            await asyncio.to_thread(shutil.rmtree, tmpdir, ignore_errors=True)
        # NOTE: no manual _download_limiter.release() — the `async with` above
        # already released it; a manual release would corrupt the semaphore.


# ---------------------------------------------------------------------------
# Background download jobs (floating progress dialog + cancel)
# ---------------------------------------------------------------------------
# POST /download (fetch/XHR branch in app.py) creates a job and returns
# immediately with a job_id; the browser then polls
# GET /download/status/<job_id> for live progress ("downloading %", "muxing",
# server speed) and finally downloads GET /download/file/<job_id>.
# POST /download/cancel/<job_id> (or closing the page) aborts the server-side
# fetch/mux and deletes the temp files.
#
# Job IDs are unguessable (uuid4), so concurrent downloads from many users are
# isolated from each other. The registry is in-memory: Granian runs a single
# worker (see python/main.py — no workers= arg), so no cross-process state or
# Redis coordination is needed for the task handles.

# Max concurrent actively-downloading/muxing jobs server-wide; extras queue.
_JOB_SLOTS = 3
# Bound active workers (running or queued) so overload is rejected before a
# task is registered. Completed jobs retained for download do not count.
_MAX_ACTIVE_JOBS = _JOB_SLOTS * 4
# Finished files stay available for re-download (retry link) this long.
_JOB_READY_TTL = 15 * 60
# Error/cancelled job records are swept after this long.
_JOB_END_TTL = 5 * 60
# Active jobs with no progress update for this long are treated as stalled.
_JOB_STALL_TIMEOUT = 30 * 60

# Job states: queued -> resolving -> downloading -> muxing -> ready
#                                                 \-> error / cancelled


_download_limiter = asyncio.Semaphore(5)


class DownloadCapacityError(RuntimeError):
    """Raised when the server-wide active download queue is full."""


class _DownloadJob:
    """Mutable per-download state. Only touched from the single event loop."""

    def __init__(self, vid: str, idx: int, qual: int):
        self.job_id = uuid.uuid4().hex
        self.vid = vid
        self.idx = idx
        self.qual = qual
        self.actual_qn = None  # quality actually being fetched (fallback of qual)
        self.state = "queued"
        self.total_bytes = 0
        self.done_bytes = 0
        self.speed_bps = 0.0
        self.status_note = None  # transient user-facing note (e.g. retry backoff)
        self.filename = _safe_download_filename(vid, idx, qual)
        self.tmpdir = None
        self.outpath = None
        self.error = None
        now = time.time()
        self.created_at = now
        self.updated_at = now
        self.cancel_event = asyncio.Event()
        self.ffmpeg_proc = None
        self.task = None
        self._samples = deque()  # (monotonic_ts, done_bytes) for speed calc

    def touch(self):
        self.updated_at = time.time()

    def throw_if_cancelled(self):
        if self.cancel_event.is_set():
            raise _JobCancelled()

    def set_note(self, msg: str | None):
        """Set/clear the transient user-facing status note (sync callback)."""
        self.status_note = msg
        self.touch()

    def rewind_progress(self, n: int):
        """Rewind progress accounting (server ignored our resume Range)."""
        self.done_bytes = max(0, self.done_bytes - n)
        self._samples.clear()
        self.speed_bps = 0.0
        self.touch()

    def add_progress(self, n: int):
        """Sync per-chunk progress callback: updates bytes + rolling speed."""
        self.done_bytes += n
        now = time.monotonic()
        samples = self._samples
        samples.append((now, self.done_bytes))
        while samples and now - samples[0][0] > 4.0:
            samples.popleft()
        if len(samples) >= 2:
            dt = samples[-1][0] - samples[0][0]
            if dt > 0.2:
                self.speed_bps = (samples[-1][1] - samples[0][1]) / dt
        self.updated_at = time.time()

    def to_status(self) -> dict:
        percent = None
        if self.total_bytes > 0:
            percent = round(min(self.done_bytes / self.total_bytes, 1.0) * 100, 1)
        return {
            "job_id": self.job_id,
            "state": self.state,
            "percent": percent,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "speed_bps": round(self.speed_bps, 1),
            "actual_qn": self.actual_qn,
            "filename": self.filename,
            "note": self.status_note,
            "error": self.error,
        }


_download_jobs: dict[str, _DownloadJob] = {}
_jobs_lock = asyncio.Lock()
_job_slots = asyncio.Semaphore(_JOB_SLOTS)


def _remove_job_tmpdir(job: _DownloadJob):
    tmpdir = job.tmpdir
    job.tmpdir = None
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _sweep_jobs():
    """Drop expired/terminal jobs and reap stalled ones. Called on every job endpoint hit."""
    now = time.time()
    async with _jobs_lock:
        for jid, job in list(_download_jobs.items()):
            age = now - job.updated_at
            if job.state == "ready" and age > _JOB_READY_TTL:
                _remove_job_tmpdir(job)
                del _download_jobs[jid]
            elif job.state in ("error", "cancelled") and age > _JOB_END_TTL:
                _remove_job_tmpdir(job)
                del _download_jobs[jid]
            elif job.state in ("queued", "resolving", "downloading", "muxing") and age > _JOB_STALL_TIMEOUT:
                # Stalled (e.g. hung upstream socket): ask the worker to abort.
                job.cancel_event.set()
                if job.task is not None and job.task.done():
                    _remove_job_tmpdir(job)
                    del _download_jobs[jid]
        # Hard registry cap: drop oldest terminal jobs first.
        if len(_download_jobs) > 100:
            terminal = sorted(
                ((j.updated_at, jid) for jid, j in _download_jobs.items() if j.state in ("ready", "error", "cancelled"))
            )
            for _, jid in terminal[: len(_download_jobs) - 100]:
                _remove_job_tmpdir(_download_jobs[jid])
                del _download_jobs[jid]


async def _get_job(job_id: str) -> _DownloadJob | None:
    if not job_id or len(job_id) > 64:
        return None
    async with _jobs_lock:
        return _download_jobs.get(job_id)


async def create_download_job(vid: str, idx: int, qual: int) -> str:
    """Register a download job and launch its background worker. Returns job_id."""
    if not _is_valid_vid(vid):
        raise RuntimeError("invalid video ID")
    await _sweep_jobs()
    async with _jobs_lock:
        active_jobs = sum(job.state not in ("ready", "error", "cancelled") for job in _download_jobs.values())
        if active_jobs >= _MAX_ACTIVE_JOBS:
            raise DownloadCapacityError("Download queue is full; please try again later")
        job = _DownloadJob(vid, idx, qual)
        job.task = asyncio.ensure_future(_run_download_job(job))
        _download_jobs[job.job_id] = job
    return job.job_id


async def cancel_download_job(job_id: str) -> dict | None:
    """Signal a job to abort. Returns its status dict, or None if unknown."""
    job = await _get_job(job_id)
    if job is None:
        return None
    if job.state not in ("ready", "error", "cancelled"):
        job.cancel_event.set()
        proc = job.ffmpeg_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
    return job.to_status()


async def _peek_content_length(
    url: str | list, headers: dict, proxy_url: str, cancel_event: asyncio.Event | None = None
) -> int:
    """Fetch only response headers to learn a track's size (0 if unknown).

    ``url`` may be a single URL or a candidate mirror list; each mirror is
    tried in order until one answers 200/206 with a Content-Length. Opens a
    throwaway connection per attempt and closes it without reading the body.
    Raises RuntimeError if the track exceeds the per-track size cap.
    """
    if isinstance(url, (list, tuple)):
        candidates = [u for u in url if isinstance(u, str) and u]
    else:
        candidates = [url] if isinstance(url, str) and url else []
    last_exc: Exception | None = None
    for cand in candidates:
        try:
            conn, resp_headers = await _open_cdn_track(cand, headers, proxy_url)
        except Exception as exc:
            last_exc = exc
            continue
        try:
            if resp_headers.status_code not in (200, 206):
                last_exc = RuntimeError(f"HTTP {resp_headers.status_code}")
                continue
            cl = (resp_headers.headers or {}).get("content-length")
            if not cl:
                return 0
            try:
                size = int(cl)
            except TypeError, ValueError:
                return 0
            if size > _max_download_track_bytes():
                raise RuntimeError(f"this instance maximum allowed download size is {_max_download_size_mb()} MB")
            return max(size, 0)
        finally:
            await conn.close()
    if last_exc is not None:
        print(f"[DashProxy] size peek failed on all mirrors: {last_exc}")
    return 0


async def _run_dash_job(job: _DownloadJob, dash_data: dict):
    """Download best video (<=1080p cap) + audio tracks, then ffmpeg-mux."""
    max_qn = min(job.qual, _FREE_DOWNLOAD_MAX_QN) if job.qual > 0 else _FREE_DOWNLOAD_MAX_QN
    video, audio = _pick_download_tracks(dash_data, max_qn)
    if not video or not audio:
        raise RuntimeError("no suitable DASH tracks for download")
    vurls = _dash_candidate_urls(video)
    aurls = _dash_candidate_urls(audio)
    if not vurls or not aurls:
        raise RuntimeError("track has no URL")
    v_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in vurls))
    a_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in aurls))
    vurls = [u for u, ok in zip(vurls, v_checks, strict=False) if ok]
    aurls = [u for u, ok in zip(aurls, a_checks, strict=False) if ok]
    if not vurls or not aurls:
        raise RuntimeError("invalid CDN target")

    proxy_url = Network.get_proxy()
    headers = await _build_dash_cdn_headers()

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix=f"miku_dl_{job.job_id}_")
    job.tmpdir = tmpdir
    vpath = os.path.join(tmpdir, "video.m4s")
    apath = os.path.join(tmpdir, "audio.m4s")
    outpath = os.path.join(tmpdir, "out.mp4")
    job.outpath = outpath
    actual_qn = int(video.get("id") or max_qn)
    job.actual_qn = actual_qn
    job.filename = _safe_download_filename(job.vid, job.idx, actual_qn)

    vsize = await _peek_content_length(vurls, headers, proxy_url)
    job.throw_if_cancelled()
    asize = await _peek_content_length(aurls, headers, proxy_url)
    job.throw_if_cancelled()
    job.total_bytes = vsize + asize
    job.state = "downloading"
    job.touch()

    try:
        v_qn = int(video.get("id") or 0)
    except TypeError, ValueError:
        v_qn = 0
    try:
        v_cid = int(video.get("codecid") or 0)
    except TypeError, ValueError:
        v_cid = 0
    try:
        a_qn = int(audio.get("id") or 0)
    except TypeError, ValueError:
        a_qn = 0
    try:
        a_cid = int(audio.get("codecid") or 0)
    except TypeError, ValueError:
        a_cid = 0

    async def _refresh_job_track_urls(media_type: str, qn: int, cid: int):
        """Fresh-playurl mirrors for the *same* track (byte-identical content).

        Returns a safety-checked candidate list, or None. A refreshed
        response that no longer carries this exact track id is declined:
        switching qualities mid-file would corrupt the download.
        """
        fresh = await _refresh_dash_data(job.vid, job.idx)
        if not has_valid_dash_tracks(fresh):
            return None
        track = _lookup_track(fresh, media_type, qn, cid)
        if track is None:
            pick = _pick_download_tracks(fresh, max_qn)
            track = pick[0] if media_type == "video" else pick[1]
        if track is None:
            return None
        try:
            if int(track.get("id") or -1) != qn:
                print(
                    f"[DashProxy] job {job.job_id} refresh changed track id "
                    f"(want {qn}, got {track.get('id')}), declining"
                )
                return None
        except TypeError, ValueError:
            return None
        urls = _dash_candidate_urls(track)
        checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls))
        urls = [u for u, ok in zip(urls, checks, strict=False) if ok]
        return urls or None

    async def _refresh_video_urls():
        return await _refresh_job_track_urls("video", v_qn, v_cid)

    async def _refresh_audio_urls():
        return await _refresh_job_track_urls("audio", a_qn, a_cid)

    # Video + audio fetch concurrently (like dash.js's two adaptation sets):
    # independent files, shared progress/cancel accounting. All shared-state
    # updates are synchronous (no awaits inside), so coroutines cannot
    # interleave mid-update on the single event loop. Callees never raise
    # (they return byte counts / _DOWNLOAD_CANCELLED), so gather is safe.
    vn, an = await asyncio.gather(
        _download_track_file(
            vurls,
            headers,
            proxy_url,
            vpath,
            vsize,
            f"{job.vid}:{job.idx} video",
            progress_cb=job.add_progress,
            cancel_event=job.cancel_event,
            note_cb=job.set_note,
            rewind_cb=job.rewind_progress,
            refresh_cb=_refresh_video_urls,
        ),
        _download_track_file(
            aurls,
            headers,
            proxy_url,
            apath,
            asize,
            f"{job.vid}:{job.idx} audio",
            progress_cb=job.add_progress,
            cancel_event=job.cancel_event,
            note_cb=job.set_note,
            rewind_cb=job.rewind_progress,
            refresh_cb=_refresh_audio_urls,
        ),
    )
    if vn == _DOWNLOAD_CANCELLED or an == _DOWNLOAD_CANCELLED:
        raise _JobCancelled()
    job.throw_if_cancelled()
    if vn < 0:
        raise RuntimeError("video track download failed")
    if an < 0:
        raise RuntimeError("audio track download failed")

    job.state = "muxing"
    job.set_note(None)
    job.speed_bps = 0.0
    job.touch()
    await _mux_tracks(vpath, apath, outpath, job=job)
    for p in (vpath, apath):
        try:
            await asyncio.to_thread(os.remove, p)
        except Exception:
            pass


async def _run_durl_job(job: _DownloadJob):
    """Download fallback for durl-only videos: progressive MP4, no mux step."""
    from api import video as video_mod

    try:
        v = video_mod.Video(bvid=job.vid, credential=appcred)
    except Exception:
        raise RuntimeError("invalid video ID") from None
    try:
        async with asyncio.timeout(DASH_FETCH_TIMEOUT):
            play_data = await video_get_dash_for_qn(v, job.idx)
    except Exception as exc:
        print(f"[DashProxy] job {job.job_id} durl fetch failed: {exc}")
        play_data = None
    job.throw_if_cancelled()
    try:
        url, qn, ext = await _resolve_durl_download(v, job.vid, job.idx, job.qual, play_data=play_data)
    except _DurlResolveError as exc:
        raise RuntimeError(str(exc)) from None
    job.actual_qn = qn
    durl_urls = [url]
    try:
        bak = await appredis.get(f"mikuinv_{job.vid}_{job.idx}_{qn}_bak")
        if bak:
            if isinstance(bak, bytes):
                bak = bak.decode()
            if bak and bak != url and _is_safe_dash_url(bak):
                durl_urls.append(bak)
    except Exception:
        pass
    safe_checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in durl_urls))
    durl_urls = [u for u, ok in zip(durl_urls, safe_checks, strict=False) if ok]
    if not durl_urls:
        raise RuntimeError("invalid CDN target")

    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix=f"miku_dl_{job.job_id}_")
    job.tmpdir = tmpdir
    outpath = os.path.join(tmpdir, f"out{ext}")
    job.outpath = outpath
    job.filename = _safe_download_filename(job.vid, job.idx, qn, ext)

    headers = await _build_dash_cdn_headers()
    proxy_url = Network.get_proxy()
    job.total_bytes = await _peek_content_length(durl_urls, headers, proxy_url)
    job.throw_if_cancelled()
    job.state = "downloading"
    job.touch()

    async def _refresh_durl_urls():
        """Re-resolve fresh progressive URLs for the *same* quality.

        Drops the quality-list cache so every quality is re-fetched (never
        reusing the possibly-stale initial ``durl``), then resolves again.
        A refreshed response pointing at a different quality is declined:
        switching files mid-download would corrupt it.
        """
        try:
            await appredis.delete(f"mikuinv_{job.vid}_{job.idx}")
        except Exception:
            pass
        ladder = {
            "support_formats": (play_data or {}).get("support_formats") or [],
            "quality": job.qual,
            "durl": None,
        }
        try:
            url2, qn2, _ext2 = await _resolve_durl_download(v, job.vid, job.idx, job.qual, play_data=ladder)
        except _DurlResolveError:
            return None
        if qn2 != qn:
            print(f"[DashProxy] job {job.job_id} refresh changed quality (want {qn}, got {qn2}), declining")
            return None
        urls2 = [url2]
        try:
            bak2 = await appredis.get(f"mikuinv_{job.vid}_{job.idx}_{qn2}_bak")
            if bak2:
                if isinstance(bak2, bytes):
                    bak2 = bak2.decode()
                if bak2 and bak2 != url2 and _is_safe_dash_url(bak2):
                    urls2.append(bak2)
        except Exception:
            pass
        checks = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls2))
        urls2 = [u for u, ok in zip(urls2, checks, strict=False) if ok]
        return urls2 or None

    n = await _download_track_file(
        durl_urls,
        headers,
        proxy_url,
        outpath,
        job.total_bytes,
        f"{job.vid}:{job.idx} progressive",
        progress_cb=job.add_progress,
        cancel_event=job.cancel_event,
        note_cb=job.set_note,
        rewind_cb=job.rewind_progress,
        refresh_cb=_refresh_durl_urls,
    )
    if n == _DOWNLOAD_CANCELLED:
        raise _JobCancelled()
    if n < 0:
        raise RuntimeError("progressive download failed")


async def _run_download_job(job: _DownloadJob):
    """Background worker: resolve -> download (-> mux) -> ready. Never raises."""
    acquired = False
    try:
        await _job_slots.acquire()
        acquired = True
        job.throw_if_cancelled()
        job.state = "resolving"
        job.touch()
        dash_data = await _load_dash_data(job.vid, job.idx)
        job.throw_if_cancelled()
        if has_valid_dash_tracks(dash_data):
            await _run_dash_job(job, dash_data)
        else:
            await _run_durl_job(job)
        job.throw_if_cancelled()
        if not job.outpath or not os.path.exists(job.outpath):
            raise RuntimeError("finished file missing")
        job.state = "ready"
        job.set_note(None)
        job.speed_bps = 0.0
        job.touch()
        print(f"[DashProxy] download job {job.job_id} ready: {job.filename}")
    except _JobCancelled:
        job.state = "cancelled"
        job.touch()
        print(f"[DashProxy] download job {job.job_id} cancelled")
    except Exception as exc:
        job.state = "error"
        job.error = str(exc)[:300]
        job.touch()
        print(f"[DashProxy] download job {job.job_id} error: {exc}")
    finally:
        if acquired:
            _job_slots.release()
        if job.state in ("error", "cancelled"):
            await asyncio.to_thread(_remove_job_tmpdir, job)
        job.touch()


@dash_proxy_bp.route("/download/status/<job_id>")
@rate_limit(**RATE_LIMITS["proxy"])
async def download_status(job_id):
    """Live progress for a download job (polled by the floating dialog)."""
    await _sweep_jobs()
    job = await _get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found or expired"}), 404
    return jsonify(job.to_status())


@dash_proxy_bp.route("/download/file/<job_id>")
@rate_limit(**RATE_LIMITS["proxy"])
async def download_file(job_id):
    """Stream the finished file as an attachment. Kept until TTL for retries."""
    await _sweep_jobs()
    job = await _get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found or expired"}), 404
    if job.state != "ready":
        return jsonify(job.to_status()), 409
    outpath = job.outpath
    if not outpath or not os.path.exists(outpath):
        return jsonify({"error": "file no longer available"}), 410
    try:
        size = await asyncio.to_thread(os.path.getsize, outpath)
    except OSError:
        return jsonify({"error": "file no longer available"}), 410

    async def generate():
        f = await asyncio.to_thread(open, outpath, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(f.read, 512 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

    resp = Response(generate())
    resp.headers["Content-Type"] = "video/mp4"
    resp.headers["Content-Length"] = str(size)
    safe_name = _safe_download_filename(job.vid, job.idx, job.actual_qn or job.qual)
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@dash_proxy_bp.route("/download/cancel/<job_id>", methods=["POST"])
@csrf_protect()
@rate_limit(**RATE_LIMITS["normal"])
async def download_cancel(job_id):
    """Abort a running/queued job and delete its temp files."""
    await _sweep_jobs()
    status = await cancel_download_job(job_id)
    if status is None:
        return jsonify({"error": "job not found or expired"}), 404
    return jsonify(status)
