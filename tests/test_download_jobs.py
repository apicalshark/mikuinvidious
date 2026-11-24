# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.dash.download_job as download_job
import miku.dash.download_track as download_track
import miku.dash.fetch as dash_fetch
import miku.state as shared
from miku.app import app
from miku.core.config import downloads_allowed, get_default_config


class TestDownloadJobs(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with download_job._jobs_lock:
            download_job._download_jobs.clear()

    async def asyncTearDown(self):
        await download_job.shutdown_download_jobs()

    async def test_download_file_flv_mp4_m4a_and_flac_mime_and_filename(self):
        tmpdir = tempfile.mkdtemp(prefix="miku_test_dl_mime_")
        try:
            for ext, expected_mime in (
                (".flv", "video/x-flv"),
                (".mp4", "video/mp4"),
                (".mkv", "video/x-matroska"),
                (".m4a", "audio/mp4"),
                (".flac", "audio/flac"),
            ):
                with self.subTest(ext=ext):
                    job = download_job._DownloadJob("BV1xx411c7mD", 0, 80)
                    job.state = "ready"
                    job.actual_qn = 80
                    job.tmpdir = tmpdir
                    job.outpath = os.path.join(tmpdir, f"out{ext}")
                    with open(job.outpath, "wb") as f:
                        f.write(b"dummy-media")

                    async with download_job._jobs_lock:
                        download_job._download_jobs[job.job_id] = job

                    async with app.test_request_context(f"/download/file/{job.job_id}"):
                        resp = await download_job.download_file(job.job_id)

                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp.headers.get("Content-Type"), expected_mime)
                    self.assertIn(f'{ext}"', resp.headers.get("Content-Disposition", ""))
                    data = b"".join([c async for c in resp.response])
                    self.assertEqual(data, b"dummy-media")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def test_shared_job_slots_caps_proxy_download_and_jobs_together(self):
        self.assertFalse(hasattr(download_job, "_download_limiter"))

        # Hold 1 slot via proxy_download and _JOB_SLOTS - 1 via background jobs, then check that
        # another worker blocks until a slot is released.
        gate = asyncio.Event()
        entered = 0
        max_concurrent = 0
        all_entered = asyncio.Event()

        async def gated_download_track(*args, **kwargs):
            nonlocal entered, max_concurrent
            entered += 1
            max_concurrent = max(max_concurrent, entered)
            if entered >= 2:
                all_entered.set()
            await gate.wait()
            entered -= 1
            return 100

        dash_data = {
            "dash": {
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
            }
        }

        async def run_proxy_dl():
            async with app.test_request_context("/proxy/download/BV1xx411c7mD/0/80"):
                return await download_job.proxy_download("BV1xx411c7mD", 0, 80)

        with (
            patch.object(download_job, "downloads_allowed", return_value=True),
            patch.object(download_job, "_load_dash_data", AsyncMock(return_value=dash_data)),
            patch.object(download_job, "_build_dash_cdn_headers", AsyncMock(return_value={"User-Agent": "test"})),
            patch.object(download_job, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
            patch.object(download_job, "_peek_content_length", AsyncMock(return_value=100)),
            patch.object(download_job, "_download_track_file", side_effect=gated_download_track),
            patch.object(download_job, "_mux_tracks", AsyncMock(return_value=None)),
        ):
            # Occupy _JOB_SLOTS - 1 directly on the shared semaphore
            acquired = []
            for _ in range(download_job._JOB_SLOTS - 1):
                await download_job._job_slots.acquire()
                acquired.append(True)
            try:
                t1 = asyncio.create_task(run_proxy_dl())
                async with asyncio.timeout(5.0):
                    await all_entered.wait()
                self.assertEqual(download_job._job_slots._value, 0)

                # Another acquirer waiting on _job_slots cannot enter until gate releases
                second_entered = asyncio.Event()

                async def wait_second():
                    async with download_job._job_slots:
                        second_entered.set()

                t2 = asyncio.create_task(wait_second())
                await asyncio.sleep(0.02)
                self.assertFalse(second_entered.is_set())

                gate.set()
                resp = await t1
                await t2
                self.assertTrue(second_entered.is_set())
                self.assertEqual(resp.status_code, 200)
            finally:
                for _ in acquired:
                    download_job._job_slots.release()

    async def test_shutdown_download_jobs_cancels_tasks_kills_ffmpeg_and_cleans_ready_tmpdirs(self):
        active_dir = tempfile.mkdtemp(prefix="miku_test_active_")
        ready_dir = tempfile.mkdtemp(prefix="miku_test_ready_")
        cancelled_flag = False

        async def stalled_worker():
            nonlocal cancelled_flag
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled_flag = True
                raise

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=-9)

        active_job = download_job._DownloadJob("BV1xx411c7mD", 0, 80)
        active_job.state = "muxing"
        active_job.tmpdir = active_dir
        active_job.ffmpeg_proc = fake_proc
        active_job.task = asyncio.create_task(stalled_worker())
        await asyncio.sleep(0)

        ready_job = download_job._DownloadJob("BV1xx411c7mD", 1, 64)
        ready_job.state = "ready"
        ready_job.tmpdir = ready_dir
        ready_job.outpath = os.path.join(ready_dir, "out.mp4")
        with open(ready_job.outpath, "wb") as f:
            f.write(b"ready-bytes")

        async with download_job._jobs_lock:
            download_job._download_jobs[active_job.job_id] = active_job
            download_job._download_jobs[ready_job.job_id] = ready_job

        await download_job.shutdown_download_jobs()

        self.assertTrue(cancelled_flag)
        self.assertTrue(active_job.cancel_event.is_set())
        self.assertEqual(active_job.state, "cancelled")
        fake_proc.kill.assert_called_once()
        self.assertFalse(os.path.exists(active_dir))
        self.assertFalse(os.path.exists(ready_dir))
        self.assertEqual(download_job._download_jobs, {})

    async def test_mux_tracks_timeout_kills_hung_ffmpeg(self):
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()

        async def slow_communicate():
            await asyncio.sleep(5)
            return b"", b""

        fake_proc.communicate = slow_communicate
        fake_proc.wait = AsyncMock(return_value=-9)

        with (
            patch.object(download_track, "_MUX_TIMEOUT", 0.02),
            patch.object(download_track.shutil, "which", return_value="/test/ffmpeg"),
            patch.object(download_track.asyncio, "create_subprocess_exec", AsyncMock(return_value=fake_proc)),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await download_track._mux_tracks("/tmp/v.m4s", "/tmp/a.m4s", "/tmp/out.mp4", job=None)

        self.assertIn("timed out", str(ctx.exception))
        fake_proc.kill.assert_called_once()
        fake_proc.wait.assert_awaited_once()

    async def test_fetch_supports_av_id_active_cred_and_pgc_no_redirects(self):
        fake_cred = MagicMock()
        fake_cred.sessdata = "s123"
        fake_cred.get_cookies.return_value = {
            "SESSDATA": "s123",
            "bili_jct": "jct",
            "bili_ticket": "should_not_leak",
            "bili_ticket_expires": "999",
        }

        created_video_kwargs = []

        class _FakeVideo:
            def __init__(self, bvid=None, aid=None, credential=None):
                created_video_kwargs.append({"bvid": bvid, "aid": aid, "credential": credential})

            def get_bvid(self):
                return "av170001"

            def get_aid(self):
                return 170001

            async def get_pages(self):
                return [{"cid": 555}]

            async def get_dash_playurl(self, page_index=0, cid=None, **kwargs):
                return {"dash": None}

        seen_calls = []

        class _FakeHttpClient:
            async def get(self, url, params=None, cookies=None, headers=None, follow_redirects=True, timeout=None):
                seen_calls.append({"url": url, "headers": dict(headers or {}), "follow_redirects": follow_redirects})
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "code": 0,
                    "result": {
                        "video_info": {
                            "dash": {"video": [{"id": 80, "baseUrl": "https://cdn.example/v.m4s"}], "audio": []}
                        }
                    },
                }
                return resp

        with (
            patch.object(dash_fetch.shared, "appcred", fake_cred),
            patch.object(dash_fetch, "is_cred_active", return_value=True),
            patch("miku.services.credential.is_cred_active", return_value=True),
            patch.object(dash_fetch.video, "Video", _FakeVideo),
            patch.object(dash_fetch.Network, "get_async_client", AsyncMock(return_value=_FakeHttpClient())),
        ):
            data = await dash_fetch._video_get_dash_for_qn_uncached("av170001", 0, ep_id=999, cid=555)

        self.assertEqual(created_video_kwargs[0]["aid"], 170001)
        self.assertIs(created_video_kwargs[0]["credential"], fake_cred)
        self.assertIn("dash", data)
        self.assertEqual(len(seen_calls), 1)
        self.assertFalse(seen_calls[0]["follow_redirects"])
        cookie_hdr = seen_calls[0]["headers"].get("Cookie", "")
        self.assertIn("SESSDATA=s123", cookie_hdr)
        self.assertNotIn("bili_ticket", cookie_hdr)

    async def test_per_ip_active_job_limit(self):
        gate = asyncio.Event()

        async def blocked_worker(job):
            await gate.wait()

        with (
            patch.object(download_job, "downloads_allowed", return_value=True),
            patch.object(download_job, "_run_download_job", side_effect=blocked_worker),
        ):
            for _ in range(download_job._MAX_ACTIVE_JOBS_PER_IP):
                await download_job.create_download_job("BV1xx411c7mD", 0, 80, client_ip="203.0.113.10")

            with self.assertRaises(download_job.DownloadCapacityError):
                await download_job.create_download_job("BV1xx411c7mD", 0, 80, client_ip="203.0.113.10")

            other_id = await download_job.create_download_job("BV1xx411c7mD", 0, 80, client_ip="203.0.113.11")
            self.assertTrue(other_id)
            gate.set()

    async def test_ready_job_disk_quota_eviction(self):
        d1 = tempfile.mkdtemp(prefix="miku_test_quota1_")
        d2 = tempfile.mkdtemp(prefix="miku_test_quota2_")
        try:
            j1 = download_job._DownloadJob("BV1xx411c7mD", 0, 80)
            j1.state = "ready"
            j1.tmpdir = d1
            j1.outpath = os.path.join(d1, "out.mp4")
            j1.disk_bytes = 2 * 1024 * 1024
            j1.updated_at -= 10

            j2 = download_job._DownloadJob("BV1xx411c7mD", 1, 80)
            j2.state = "ready"
            j2.tmpdir = d2
            j2.outpath = os.path.join(d2, "out.mp4")
            j2.disk_bytes = 2 * 1024 * 1024

            async with download_job._jobs_lock:
                download_job._download_jobs[j1.job_id] = j1
                download_job._download_jobs[j2.job_id] = j2

            with patch.dict(shared.appconf["site"], {"max_download_disk_mb": 3}):
                await download_job._sweep_jobs()

            self.assertNotIn(j1.job_id, download_job._download_jobs)
            self.assertIn(j2.job_id, download_job._download_jobs)
            self.assertFalse(os.path.exists(d1))
            self.assertTrue(os.path.exists(d2))
        finally:
            shutil.rmtree(d1, ignore_errors=True)
            shutil.rmtree(d2, ignore_errors=True)

    async def test_sweep_skips_pinned_ready_job(self):
        d1 = tempfile.mkdtemp(prefix="miku_test_pin1_")
        d2 = tempfile.mkdtemp(prefix="miku_test_pin2_")
        try:
            j1 = download_job._DownloadJob("BV1xx411c7mD", 0, 80)
            j1.state = "ready"
            j1.tmpdir = d1
            j1.outpath = os.path.join(d1, "out.mp4")
            j1.disk_bytes = 2 * 1024 * 1024
            j1.active_streams = 1
            j1.updated_at -= 10

            j2 = download_job._DownloadJob("BV1xx411c7mD", 1, 80)
            j2.state = "ready"
            j2.tmpdir = d2
            j2.outpath = os.path.join(d2, "out.mp4")
            j2.disk_bytes = 2 * 1024 * 1024

            async with download_job._jobs_lock:
                download_job._download_jobs[j1.job_id] = j1
                download_job._download_jobs[j2.job_id] = j2

            with patch.dict(shared.appconf["site"], {"max_download_disk_mb": 3}):
                await download_job._sweep_jobs()

            self.assertIn(j1.job_id, download_job._download_jobs)
            self.assertNotIn(j2.job_id, download_job._download_jobs)
            self.assertTrue(os.path.exists(d1))
            self.assertFalse(os.path.exists(d2))
        finally:
            shutil.rmtree(d1, ignore_errors=True)
            shutil.rmtree(d2, ignore_errors=True)

    async def test_cleanup_orphaned_download_dirs_spares_active_job(self):
        test_root = tempfile.mkdtemp(prefix="miku_test_tmproot_")
        orphan = tempfile.mkdtemp(prefix="miku_dl_orphan_", dir=test_root)
        active_dir = tempfile.mkdtemp(prefix="miku_dl_active_", dir=test_root)
        try:
            job = download_job._DownloadJob("BV1xx411c7mD", 0, 80)
            job.tmpdir = active_dir
            async with download_job._jobs_lock:
                download_job._download_jobs[job.job_id] = job

            with patch.object(download_job.tempfile, "gettempdir", return_value=test_root):
                removed = await download_job.cleanup_orphaned_download_dirs()
            self.assertEqual(removed, 1)
            self.assertFalse(os.path.exists(orphan))
            self.assertTrue(os.path.exists(active_dir))
        finally:
            shutil.rmtree(test_root, ignore_errors=True)

    async def test_run_download_job_errors_when_single_output_exceeds_disk_quota(self):
        tmpdir = tempfile.mkdtemp(prefix="miku_test_over_")
        outpath = os.path.join(tmpdir, "out.mp4")
        with open(outpath, "wb") as f:
            f.write(b"x" * (2 * 1024 * 1024))

        job = download_job._DownloadJob("BV1xx411c7mD", 0, 80)

        async def fake_durl(j):
            j.tmpdir = tmpdir
            j.outpath = outpath

        try:
            with (
                patch.object(download_job, "_load_dash_data", AsyncMock(return_value={})),
                patch.object(download_job, "_run_durl_job", fake_durl),
                patch.dict(shared.appconf["site"], {"max_download_disk_mb": 1}),
            ):
                await download_job._run_download_job(job)

            self.assertEqual(job.state, "error")
            self.assertIn("disk quota", job.error or "")
            self.assertFalse(os.path.exists(tmpdir))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def test_run_download_job_respects_cancel_during_getsize(self):
        tmpdir = tempfile.mkdtemp(prefix="miku_test_cancel_")
        outpath = os.path.join(tmpdir, "out.mp4")
        with open(outpath, "wb") as f:
            f.write(b"1234")

        job = download_job._DownloadJob("BV1xx411c7mD", 0, 80)

        async def fake_durl(j):
            j.tmpdir = tmpdir
            j.outpath = outpath

        orig_getsize = os.path.getsize

        def cancel_on_getsize(path):
            job.cancel_event.set()
            return orig_getsize(path)

        try:
            with (
                patch.object(download_job, "_load_dash_data", AsyncMock(return_value={})),
                patch.object(download_job, "_run_durl_job", fake_durl),
                patch.object(download_job.os.path, "getsize", cancel_on_getsize),
            ):
                await download_job._run_download_job(job)

            self.assertEqual(job.state, "cancelled")
            self.assertFalse(os.path.exists(tmpdir))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def test_download_file_releases_pin_on_cancellation(self):
        tmpdir = tempfile.mkdtemp(prefix="miku_test_cancel_pin_")
        outpath = os.path.join(tmpdir, "out.mp4")
        with open(outpath, "wb") as f:
            f.write(b"1234")

        job = download_job._DownloadJob("BV1xx411c7mD", 0, 80)
        job.state = "ready"
        job.tmpdir = tmpdir
        job.outpath = outpath

        async with download_job._jobs_lock:
            download_job._download_jobs[job.job_id] = job

        async def cancel_to_thread(fn, *args, **kwargs):
            if fn is os.path.getsize:
                raise asyncio.CancelledError()
            return fn(*args, **kwargs)

        try:
            with (
                patch.object(download_job.asyncio, "to_thread", cancel_to_thread),
                patch.dict(shared.appconf["rate_limit"], {"enabled": False}),
            ):
                from quart import Quart

                app = Quart(__name__)
                app.register_blueprint(download_job.dash_proxy_bp)
                async with app.test_request_context(f"/download/file/{job.job_id}"):
                    with self.assertRaises(asyncio.CancelledError):
                        await download_job.download_file(job.job_id)

            self.assertEqual(job.active_streams, 0)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def test_download_file_delivery_cleanup_is_idempotent(self):
        for started in (False, True):
            with self.subTest(started=started), tempfile.TemporaryDirectory() as tmpdir:
                job = download_job._DownloadJob("BV1xx411c7mD", 0, 80)
                job.state = "ready"
                job.outpath = os.path.join(tmpdir, "out.mp4")
                with open(job.outpath, "wb") as f:
                    f.write(b"media")
                download_job._download_jobs[job.job_id] = job
                ready = asyncio.Event()
                response = None

                async def deliver(job=job, started=started, ready=ready):
                    nonlocal response
                    async with app.test_request_context(f"/download/file/{job.job_id}"):
                        response = await download_job.download_file(job.job_id)
                    if started:
                        async with response.response as body:
                            self.assertEqual(await anext(aiter(body)), b"media")
                            ready.set()
                            await asyncio.Future()
                    else:
                        ready.set()
                        await asyncio.Future()

                with patch.dict(shared.appconf["rate_limit"], {"enabled": False}):
                    task = asyncio.create_task(deliver())
                    await asyncio.wait_for(ready.wait(), 5)
                    self.assertEqual(job.active_streams, 1)
                    job.active_streams += 1
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertEqual(job.active_streams, 1)
                    await response.response.__aexit__(None, None, None)
                    await response.response.__aexit__(None, None, None)
                    self.assertEqual(job.active_streams, 1)

    async def test_legacy_admission_released_when_header_initialization_cancelled(self):
        ip = "198.51.100.78"
        track = {"id": 80, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/track.m4s"}
        with (
            patch.dict(shared.appconf["rate_limit"], {"enabled": False}),
            patch.object(download_job, "downloads_allowed", return_value=True),
            patch.object(download_job, "get_client_ip", return_value=ip),
            patch.object(download_job, "_load_dash_data", AsyncMock(return_value={})),
            patch.object(download_job, "has_valid_dash_tracks", return_value=True),
            patch.object(download_job, "_pick_download_tracks", return_value=(track, track)),
            patch.object(download_job, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
            patch.object(download_job, "_build_dash_cdn_headers", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            async with app.test_request_context("/proxy/download/BV1xx411c7mD/0/80"):
                with self.assertRaises(asyncio.CancelledError):
                    await download_job.proxy_download("BV1xx411c7mD", 0, 80)
        self.assertNotIn(ip, download_job._legacy_ip_active)

    async def test_legacy_and_background_downloads_share_per_ip_limit(self):
        ip = "198.51.100.77"
        try:
            self.assertTrue(await download_job._try_acquire_legacy_ip_admission(ip))
            self.assertTrue(await download_job._try_acquire_legacy_ip_admission(ip))
            self.assertFalse(await download_job._try_acquire_legacy_ip_admission(ip))
            with self.assertRaises(download_job.DownloadCapacityError):
                await download_job.create_download_job("BV1xx411c7mD", 0, 80, client_ip=ip)
            download_job._release_legacy_ip_admission(ip)
            download_job._release_legacy_ip_admission(ip)
            self.assertNotIn(ip, download_job._legacy_ip_active)
        finally:
            download_job._legacy_ip_active.pop(ip, None)

    async def test_extract_download_options_and_exact_track_selection(self):
        dash_data = {
            "support_formats": [
                {"quality": 112, "new_description": "1080P+"},
                {"quality": 80, "new_description": "1080P"},
                {"quality": 64, "new_description": "720P"},
            ],
            "dash": {
                "duration": 100,
                "video": [
                    {
                        "id": 112,
                        "codecid": 7,
                        "width": 1920,
                        "height": 1080,
                        "bandwidth": 3000000,
                        "codecs": "avc1.640028",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v112.m4s",
                    },
                    {
                        "id": 80,
                        "codecid": 7,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "60",
                        "bandwidth": 2000000,
                        "codecs": "avc1.640028",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v80_avc.m4s",
                    },
                    {
                        "id": 80,
                        "codecid": 12,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "60",
                        "bandwidth": 1400000,
                        "codecs": "hev1.1.6.L150.90",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v80_hevc.m4s",
                    },
                    {
                        "id": 80,
                        "codecid": 13,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "60",
                        "bandwidth": 1100000,
                        "codecs": "av01.0.08M.08",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v80_av1.m4s",
                    },
                    {
                        "id": 64,
                        "codecid": 13,
                        "width": 1280,
                        "height": 720,
                        "bandwidth": 800000,
                        "codecs": "av01.0.08M.08",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v64_av1.m4s",
                    },
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 192000,
                        "codecs": "mp4a.40.2",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a30280.m4s",
                    },
                    {
                        "id": 30216,
                        "codecid": 0,
                        "bandwidth": 64000,
                        "codecs": "mp4a.40.5",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a30216.m4s",
                    },
                ],
                "dolby": {
                    "audio": [
                        {
                            "id": 30250,
                            "codecid": 0,
                            "bandwidth": 384000,
                            "codecs": "ec-3",
                            "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/dolby.m4s",
                        }
                    ]
                },
                "flac": {
                    "audio": {
                        "id": 30251,
                        "codecid": 0,
                        "bandwidth": 1200000,
                        "codecs": "fLaC",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/flac.m4s",
                    }
                },
            },
        }
        opts = download_track.extract_download_options(dash_data)
        self.assertEqual([v["id"] for v in opts["videos"]], [80, 80, 80, 64])
        self.assertEqual([v["codec_label"] for v in opts["videos"]], ["AV1", "HEVC", "AVC", "AV1"])
        self.assertEqual(opts["videos"][0]["description"], "1080p")
        self.assertEqual(opts["videos"][0]["size_bytes"], 13750000)
        self.assertEqual([a["id"] for a in opts["audios"]], [30251, 30250, 30280, 30216])
        self.assertEqual([a["codec_label"] for a in opts["audios"]], ["FLAC", "Dolby", "AAC", "HE-AAC"])
        self.assertEqual([a["container"] for a in opts["audios"]], ["flac", "m4a", "m4a", "m4a"])

        v_picked, a_picked = download_track._pick_download_tracks(dash_data, 80, video_cid=12)
        self.assertEqual(v_picked["codecid"], 12)
        self.assertEqual(a_picked["id"], 30280)

        flac_picked = download_track._pick_audio_track(dash_data, audio_qn=30251, audio_cid=0)
        self.assertEqual(flac_picked["id"], 30251)

        self.assertEqual(dash_fetch._lookup_track(dash_data, "audio", 30250, 0)["id"], 30250)
        self.assertEqual(dash_fetch._lookup_track(dash_data, "audio", 30251, 0)["id"], 30251)

    async def test_remux_audio_track_command_args_and_audio_job_execution(self):
        recorded_cmds = []
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def capture_exec(*cmd, **kwargs):
            recorded_cmds.append(list(cmd))
            return fake_proc

        with (
            patch.object(download_track.shutil, "which", return_value="/usr/bin/ffmpeg"),
            patch.object(download_track.asyncio, "create_subprocess_exec", side_effect=capture_exec),
        ):
            await download_track._remux_audio_track("/tmp/a.m4s", "/tmp/out.m4a", is_flac=False)
            await download_track._remux_audio_track("/tmp/a.m4s", "/tmp/out.flac", is_flac=True)

        self.assertIn("-movflags", recorded_cmds[0])
        self.assertNotIn("-movflags", recorded_cmds[1])

        dash_data = {
            "dash": {
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 192000,
                        "codecs": "mp4a.40.2",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
            }
        }
        job = download_job._DownloadJob("BV1xx411c7mD", 0, 30280, mode="audio", audio_qn=30280, audio_cid=0)
        dl_mock = AsyncMock(return_value=128)
        remux_mock = AsyncMock()
        try:
            with (
                patch.object(download_job, "_build_dash_cdn_headers", AsyncMock(return_value={})),
                patch.object(download_job, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
                patch.object(download_job, "_peek_content_length", AsyncMock(return_value=128)),
                patch.object(download_job, "_download_track_file", dl_mock),
                patch.object(download_job, "_remux_audio_track", remux_mock),
            ):
                await download_job._run_dash_job(job, dash_data)
            dl_mock.assert_awaited_once()
            remux_mock.assert_awaited_once()
            self.assertEqual(job.actual_qn, 30280)
            self.assertTrue(job.filename.endswith("_0_a30280.m4a"))
            self.assertEqual(job.resolved, {"mode": "audio", "bitrate": 192, "codec": "AAC", "container": "m4a"})
        finally:
            download_job._remove_job_tmpdir(job)

    async def test_post_download_route_supports_audio_mode_and_music_ids(self):
        with (
            patch.dict(shared.appconf["site"], {"site_allow_download": True}),
            patch.dict(shared.appconf["rate_limit"], {"enabled": False}),
            patch("miku.core.csrf.validate_csrf_token", return_value=True),
            patch("miku.dash.create_download_job", new=AsyncMock(return_value="job-123")) as create_mock,
        ):
            client = app.test_client()
            resp = await client.post(
                "/download",
                form={
                    "csrf_token": "tok",
                    "id": "BV1xx411c7mD",
                    "cvid": "0",
                    "qual": "30280",
                    "mode": "audio",
                    "a_qn": "30280",
                    "a_cid": "0",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(await resp.get_json(), {"job_id": "job-123"})
            self.assertEqual(create_mock.await_args.kwargs["mode"], "audio")
            self.assertEqual(create_mock.await_args.kwargs["audio_qn"], 30280)

            resp_au = await client.post(
                "/download",
                form={"csrf_token": "tok", "id": "au12345", "cvid": "0", "qual": "0", "mode": "audio"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            self.assertEqual(resp_au.status_code, 200)
            self.assertEqual(await resp_au.get_json(), {"direct_url": "/proxy/video/au12345_0_0?dl=1"})

            resp_am = await client.post(
                "/download",
                form={"csrf_token": "tok", "id": "am67890", "cvid": "2", "qual": "0", "mode": "audio"},
            )
            self.assertEqual(resp_am.status_code, 302)
            self.assertEqual(resp_am.headers.get("Location"), "/proxy/video/am67890_2_0?dl=1")

            resp_vo = await client.post(
                "/download",
                form={
                    "csrf_token": "tok",
                    "id": "BV1xx411c7mD",
                    "cvid": "0",
                    "qual": "80",
                    "mode": "video_only",
                    "v_cid": "12",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            self.assertEqual(resp_vo.status_code, 200)
            self.assertEqual(create_mock.await_args.kwargs["mode"], "video_only")
            self.assertEqual(create_mock.await_args.kwargs["video_cid"], 12)

            resp_combo = await client.post(
                "/download",
                form={
                    "csrf_token": "tok",
                    "id": "BV1xx411c7mD",
                    "cvid": "0",
                    "qual": "80",
                    "mode": "video",
                    "v_cid": "12",
                    "a_qn": "30251",
                    "a_cid": "0",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            self.assertEqual(resp_combo.status_code, 200)
            self.assertEqual(create_mock.await_args.kwargs["mode"], "video")
            self.assertEqual(create_mock.await_args.kwargs["video_cid"], 12)
            self.assertEqual(create_mock.await_args.kwargs["audio_qn"], 30251)
            self.assertEqual(create_mock.await_args.kwargs["audio_cid"], 0)

    async def test_video_only_remux_and_video_flac_mkv_job(self):
        recorded_cmds = []
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def capture_exec(*cmd, **kwargs):
            recorded_cmds.append(list(cmd))
            return fake_proc

        with (
            patch.object(download_track.shutil, "which", return_value="/usr/bin/ffmpeg"),
            patch.object(download_track.asyncio, "create_subprocess_exec", side_effect=capture_exec),
        ):
            await download_track._mux_tracks("/tmp/v.m4s", "/tmp/a.m4s", "/tmp/out.mp4", is_mkv=False)
            await download_track._mux_tracks("/tmp/v.m4s", "/tmp/a.m4s", "/tmp/out.mkv", is_mkv=True)
            await download_track._remux_video_track("/tmp/v.m4s", "/tmp/vo.mp4")

        self.assertIn("-movflags", recorded_cmds[0])
        self.assertNotIn("-movflags", recorded_cmds[1])
        self.assertIn("0:v:0", recorded_cmds[2])
        self.assertIn("-movflags", recorded_cmds[2])

        dash_data = {
            "dash": {
                "video": [
                    {
                        "id": 80,
                        "codecid": 12,
                        "height": 1080,
                        "codecs": "hev1.1.6.L120.90",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v_hevc.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 192000,
                        "codecs": "mp4a.40.2",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s",
                    }
                ],
                "flac": {
                    "audio": {
                        "id": 30251,
                        "codecid": 0,
                        "bandwidth": 1200000,
                        "codecs": "fLaC",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/flac.m4s",
                    }
                },
            }
        }

        job_vo = download_job._DownloadJob("BV1xx411c7mD", 0, 80, mode="video_only", video_cid=12)
        dl_mock = AsyncMock(return_value=256)
        remux_video_mock = AsyncMock()
        try:
            with (
                patch.object(download_job, "_build_dash_cdn_headers", AsyncMock(return_value={})),
                patch.object(download_job, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
                patch.object(download_job, "_peek_content_length", AsyncMock(return_value=256)),
                patch.object(download_job, "_download_track_file", dl_mock),
                patch.object(download_job, "_remux_video_track", remux_video_mock),
            ):
                await download_job._run_dash_job(job_vo, dash_data)
            dl_mock.assert_awaited_once()
            remux_video_mock.assert_awaited_once()
            self.assertTrue(job_vo.filename.endswith("_0_p80.mp4"))
            self.assertEqual(
                job_vo.resolved,
                {"mode": "video_only", "height": 1080, "codec": "HEVC", "container": "mp4"},
            )
        finally:
            download_job._remove_job_tmpdir(job_vo)

        job_mkv = download_job._DownloadJob(
            "BV1xx411c7mD", 0, 80, mode="video", video_cid=12, audio_qn=30251, audio_cid=0
        )
        dl_combo_mock = AsyncMock(return_value=256)
        mux_mock = AsyncMock()
        try:
            with (
                patch.object(download_job, "_build_dash_cdn_headers", AsyncMock(return_value={})),
                patch.object(download_job, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
                patch.object(download_job, "_peek_content_length", AsyncMock(return_value=256)),
                patch.object(download_job, "_download_track_file", dl_combo_mock),
                patch.object(download_job, "_mux_tracks", mux_mock),
            ):
                await download_job._run_dash_job(job_mkv, dash_data)
            self.assertEqual(dl_combo_mock.await_count, 2)
            self.assertTrue(mux_mock.await_args.kwargs["is_mkv"])
            self.assertTrue(job_mkv.filename.endswith("_0_p80.mkv"))
            self.assertEqual(
                job_mkv.resolved,
                {
                    "mode": "video",
                    "height": 1080,
                    "codec": "HEVC",
                    "audio_bitrate": 1200,
                    "audio_codec": "FLAC",
                    "container": "mkv",
                },
            )
        finally:
            download_job._remove_job_tmpdir(job_mkv)

    async def test_durl_fallback_rejects_audio_and_video_only_modes(self):
        durl_fallback_mock = AsyncMock()
        with (
            patch.dict(shared.appconf["site"], {"site_allow_download": True}),
            patch.dict(shared.appconf["rate_limit"], {"enabled": False}),
            patch.object(download_job, "_load_dash_data", AsyncMock(return_value=None)),
            patch.object(download_job, "_proxy_download_durl_fallback", durl_fallback_mock),
        ):
            client = app.test_client()
            for mode in ("audio", "video_only"):
                resp = await client.get(f"/proxy/download/BV1xx411c7mD/0/80?mode={mode}")
                self.assertEqual(resp.status_code, 400)
                self.assertIn("DASH", await resp.get_data(as_text=True))
            durl_fallback_mock.assert_not_awaited()

        for mode in ("audio", "video_only"):
            job = download_job._DownloadJob("BV1xx411c7mD", 0, 80, mode=mode)
            with (
                patch.object(download_job, "_load_dash_data", AsyncMock(return_value=None)),
                patch.object(download_job, "_sweep_jobs", AsyncMock()),
            ):
                await download_job._run_download_job(job)
            self.assertEqual(job.state, "error")
            self.assertIn("DASH", job.error)

    async def test_dash_refresh_declines_mismatched_codecid(self):
        initial_dash = {
            "dash": {
                "video": [
                    {
                        "id": 80,
                        "codecid": 13,
                        "height": 1080,
                        "codecs": "av01.0.08M.08",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v_av1.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 192000,
                        "codecs": "mp4a.40.2",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s",
                    }
                ],
            }
        }
        mismatched_codec_dash = {
            "dash": {
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "height": 1080,
                        "codecs": "avc1.640032",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v_avc.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
                "audio": initial_dash["dash"]["audio"],
            }
        }
        matching_codec_dash = {
            "dash": {
                "video": [
                    {
                        "id": 80,
                        "codecid": 13,
                        "height": 1080,
                        "codecs": "av01.0.08M.08",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v_av1_fresh.m4s",
                        "SegmentBase": {"Initialization": "0-10", "indexRange": "11-20"},
                    }
                ],
                "audio": initial_dash["dash"]["audio"],
            }
        }

        refresh_results = []

        async def capture_download(*args, **kwargs):
            cb = kwargs.get("refresh_cb")
            if cb is not None:
                refresh_results.append(await cb())
                refresh_results.append(await cb())
            return 256

        job = download_job._DownloadJob("BV1xx411c7mD", 0, 80, mode="video_only", video_cid=13)
        try:
            with (
                patch.object(download_job, "_build_dash_cdn_headers", AsyncMock(return_value={})),
                patch.object(download_job, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
                patch.object(download_job, "_peek_content_length", AsyncMock(return_value=256)),
                patch.object(
                    download_job,
                    "_refresh_dash_data",
                    AsyncMock(side_effect=[mismatched_codec_dash, matching_codec_dash]),
                ),
                patch.object(download_job, "_download_track_file", side_effect=capture_download),
                patch.object(download_job, "_remux_video_track", AsyncMock()),
            ):
                await download_job._run_dash_job(job, initial_dash)

            self.assertIsNone(refresh_results[0])
            self.assertTrue(refresh_results[1])
            self.assertTrue(all(u.endswith("/v_av1_fresh.m4s") for u in refresh_results[1]))

            legacy_refresh_results = []

            async def capture_legacy_download(*args, **kwargs):
                cb = kwargs.get("refresh_cb")
                if cb is not None:
                    legacy_refresh_results.append(await cb())
                    legacy_refresh_results.append(await cb())
                return 256

            async def fake_remux_video(vpath, outpath, **kwargs):
                with open(outpath, "wb") as f:
                    f.write(b"ok")

            with (
                patch.dict(shared.appconf["site"], {"site_allow_download": True}),
                patch.dict(shared.appconf["rate_limit"], {"enabled": False}),
                patch.object(download_job, "_load_dash_data", AsyncMock(return_value=initial_dash)),
                patch.object(download_job, "_try_acquire_legacy_ip_admission", AsyncMock(return_value=True)),
                patch.object(download_job, "_build_dash_cdn_headers", AsyncMock(return_value={})),
                patch.object(download_job, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
                patch.object(download_job, "_peek_content_length", AsyncMock(return_value=256)),
                patch.object(
                    download_job,
                    "_refresh_dash_data",
                    AsyncMock(side_effect=[mismatched_codec_dash, matching_codec_dash]),
                ),
                patch.object(download_job, "_download_track_file", side_effect=capture_legacy_download),
                patch.object(download_job, "_remux_video_track", side_effect=fake_remux_video),
            ):
                client = app.test_client()
                resp = await client.get("/proxy/download/BV1xx411c7mD/0/80?mode=video_only&v_cid=13")
                self.assertEqual(resp.status_code, 200)

            self.assertEqual(len(legacy_refresh_results), 2)
            self.assertIsNone(legacy_refresh_results[0])
            self.assertTrue(legacy_refresh_results[1])
            self.assertTrue(all(u.endswith("/v_av1_fresh.m4s") for u in legacy_refresh_results[1]))
        finally:
            download_job._remove_job_tmpdir(job)


class TestDownloadsAllowed(unittest.TestCase):
    def test_downloads_allowed_variants(self):
        self.assertTrue(downloads_allowed({"site": {"site_allow_download": True}}))
        self.assertFalse(downloads_allowed({"site": {"site_allow_download": False}}))
        self.assertTrue(downloads_allowed({"site": {"site_allow_download": "true"}}))
        self.assertTrue(downloads_allowed({"site": {"site_allow_download": " True "}}))
        self.assertFalse(downloads_allowed({"site": {"site_allow_download": "false"}}))
        self.assertFalse(downloads_allowed({"site": {"site_allow_download": "0"}}))
        self.assertTrue(downloads_allowed({}))

        with patch.dict(os.environ, {"SITE_ALLOW_DOWNLOAD": "false"}):
            conf = get_default_config()
            self.assertFalse(downloads_allowed(conf))

        with patch.dict(shared.appconf["site"], {"site_allow_download": False}):
            self.assertFalse(shared.downloads_allowed())


if __name__ == "__main__":
    unittest.main()
