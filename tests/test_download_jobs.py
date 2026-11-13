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

    async def test_download_file_flv_and_mp4_mime_and_filename(self):
        tmpdir = tempfile.mkdtemp(prefix="miku_test_dl_mime_")
        try:
            for ext, expected_mime in ((".flv", "video/x-flv"), (".mp4", "video/mp4")):
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
