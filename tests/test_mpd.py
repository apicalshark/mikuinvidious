import unittest

from miku.dash.mpd import generate_vod_mpd


class TestMpd(unittest.TestCase):
    def setUp(self):
        self.sample_dash = {
            "dash": {
                "duration": 120,
                "minBufferTime": 1.5,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "bandwidth": 1500000,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "30",
                        "codecs": "avc1.640028",
                        "SegmentBase": {
                            "Initialization": "0-999",
                            "indexRange": "1000-1500",
                        },
                    },
                    {
                        "id": 80,
                        "codecid": 12,
                        "bandwidth": 1200000,
                        "width": 1920,
                        "height": 1080,
                        "frame_rate": "30",
                        "codecs": "hev1.1.6.L150.90",
                        "SegmentBase": {
                            "initialization": {"range": "0-1024"},
                            "indexRange": "1025-1600",
                        },
                    },
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 320000,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {
                            "Initialization": "0-800",
                            "indexRange": "801-1200",
                        },
                    }
                ],
            }
        }

    def test_mpd_generation_structure(self):
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, self.sample_dash)
        self.assertIsNotNone(mpd)
        self.assertIn('<?xml version="1.0" encoding="UTF-8"?>', mpd)
        self.assertIn('mediaPresentationDuration="PT120S"', mpd)
        self.assertIn('minBufferTime="PT1.5S"', mpd)
        # 2 video adaptation sets (codecid 7 and 12) + 1 audio adaptation set
        self.assertIn('<AdaptationSet id="1" contentType="video"', mpd)
        self.assertIn('<AdaptationSet id="2" contentType="video"', mpd)
        self.assertIn('<AdaptationSet id="3" contentType="audio"', mpd)
        self.assertIn("<BaseURL>/proxy/dash/BV1xx411c7xx/0/video/80/7</BaseURL>", mpd)
        self.assertIn("<BaseURL>/proxy/dash/BV1xx411c7xx/0/video/80/12</BaseURL>", mpd)
        self.assertIn("<BaseURL>/proxy/dash/BV1xx411c7xx/0/audio/30280/0</BaseURL>", mpd)
        self.assertIn('<Initialization range="0-999"/>', mpd)
        self.assertIn('<Initialization range="0-1024"/>', mpd)
        self.assertIn('<Initialization range="0-800"/>', mpd)

    def test_empty_or_invalid_dash(self):
        self.assertIsNone(generate_vod_mpd("BV1xx411c7xx", 0, None))
        self.assertIsNone(generate_vod_mpd("BV1xx411c7xx", 0, {}))
        self.assertIsNone(generate_vod_mpd("BV1xx411c7xx", 0, {"dash": {}}))

    def test_audio_tracks_sorted_descending_by_bandwidth(self):
        dash_data = {
            "dash": {
                "duration": 60,
                "minBufferTime": 1.5,
                "audio": [
                    {
                        "id": 30216,
                        "codecid": 0,
                        "bandwidth": 51101,
                        "codecs": "mp4a.40.5",
                        "SegmentBase": {"Initialization": "0-942", "indexRange": "943-1682"},
                    },
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 320000,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {"Initialization": "0-800", "indexRange": "801-1200"},
                    },
                    {
                        "id": 30232,
                        "codecid": 0,
                        "bandwidth": 119790,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {"Initialization": "0-933", "indexRange": "934-1673"},
                    },
                ],
            }
        }
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, dash_data)
        self.assertIsNotNone(mpd)
        pos_30280 = mpd.find("audio_30280_0")
        pos_30232 = mpd.find("audio_30232_0")
        pos_30216 = mpd.find("audio_30216_0")
        self.assertTrue(pos_30280 != -1 and pos_30232 != -1 and pos_30216 != -1)
        self.assertLess(pos_30280, pos_30232)
        self.assertLess(pos_30232, pos_30216)


class TestDashHardening(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import miku.dash.mpd as dash_mpd
        import miku.dash.stream as dash_stream

        dash_stream.dash_concurrency.reset()
        dash_mpd._manifest_fresh_at.clear()

    def tearDown(self):
        import miku.dash.mpd as dash_mpd
        import miku.dash.stream as dash_stream

        dash_stream.dash_concurrency.reset()
        dash_mpd._manifest_fresh_at.clear()

    async def test_proxy_dash_rejects_missing_or_unbounded_range(self):
        from unittest.mock import AsyncMock, patch

        import miku.dash.stream as dash_stream
        from miku.app import app

        load_mock = AsyncMock()
        with patch.object(dash_stream, "_load_dash_data", load_mock):
            # 1. Missing Range header (crawler scraping BaseURL from MPD)
            async with app.test_request_context("/proxy/dash/BV1xx411c7xx/0/video/80/7"):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp.status_code, 416)
                self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")

            # 2. Open-ended Range: bytes=0-
            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/80/7",
                headers={"Range": "bytes=0-"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp.status_code, 416)

            # 3. Oversized Range (> DASH_MAX_FRAGMENT_BYTES)
            too_big = dash_stream.DASH_MAX_FRAGMENT_BYTES + 100
            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/80/7",
                headers={"Range": f"bytes=0-{too_big}"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp.status_code, 416)

            # 4. Inverted Range (end < start)
            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/80/7",
                headers={"Range": "bytes=500-100"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp.status_code, 416)

        load_mock.assert_not_awaited()

    async def test_proxy_dash_rejects_invalid_vid_or_params(self):
        from unittest.mock import AsyncMock, patch

        import miku.dash.stream as dash_stream
        from miku.app import app

        load_mock = AsyncMock()
        with patch.object(dash_stream, "_load_dash_data", load_mock):
            async with app.test_request_context(
                "/proxy/dash/not_a_valid_vid/0/video/80/7",
                headers={"Range": "bytes=0-999"},
            ):
                resp = await dash_stream.proxy_dash("not_a_valid_vid", 0, "video", 80, 7)
                self.assertEqual(resp.status_code, 400)

            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/-1/video/80/7",
                headers={"Range": "bytes=0-999"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", -1, "video", 80, 7)
                self.assertEqual(resp.status_code, 400)

        load_mock.assert_not_awaited()

    async def test_proxy_dash_allows_valid_bounded_range_and_enforces_concurrency(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        import miku.dash.stream as dash_stream
        from miku.app import app

        dash_data = {
            "dash": {
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "base_url": "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s",
                        "backup_url": [],
                    }
                ]
            }
        }
        gate = asyncio.Event()

        class SlowConn:
            async def iter_chunks(self):
                yield b"a" * 50
                await gate.wait()
                yield b"b" * 50

            async def close(self):
                pass

        async def fake_fetch(*args, **kwargs):
            resp_headers = SimpleNamespace(
                status_code=206,
                headers={"content-length": "100", "content-range": "bytes 0-99/1000"},
            )
            return (
                SlowConn(),
                resp_headers,
                "https://upos-sz-mirrorcosov.bilivideo.com/track.m4s",
                "100",
                "bytes 0-99/1000",
            )

        with (
            patch.object(dash_stream, "_load_dash_data", AsyncMock(return_value=dash_data)),
            patch.object(dash_stream, "_is_safe_dash_url_async", AsyncMock(return_value=True)),
            patch.object(dash_stream, "_build_dash_cdn_headers", AsyncMock(return_value={})),
            patch.object(dash_stream, "_fetch_dash_track", side_effect=fake_fetch),
            patch.object(
                dash_stream, "get_client_ip", side_effect=lambda req: req.headers.get("X-Real-IP", "127.0.0.1")
            ),
            patch.object(dash_stream.dash_concurrency, "max_per_ip", 2),
        ):
            # Open 2 in-flight streams from the same IP
            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/80/7",
                headers={"Range": "bytes=0-99", "X-Real-IP": "203.0.113.9"},
            ):
                resp1 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp1.status_code, 206)

            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/64/7",
                headers={"Range": "bytes=0-99", "X-Real-IP": "203.0.113.9"},
            ):
                resp2 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp2.status_code, 206)

            # 3rd concurrent stream from the same IP is rejected with 429
            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/32/7",
                headers={"Range": "bytes=0-99", "X-Real-IP": "203.0.113.9"},
            ):
                resp3 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp3.status_code, 429)
                self.assertEqual(resp3.headers.get("Retry-After"), "1")

            # Another IP can still stream normally
            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/80/7",
                headers={"Range": "bytes=0-99", "X-Real-IP": "198.51.100.7"},
            ):
                resp_other = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp_other.status_code, 206)

            # Consume resp1 to completion -> frees 1 slot for 203.0.113.9
            gate.set()
            async with resp1.response as body1:
                data1 = b"".join([c async for c in aiter(body1)])
            self.assertEqual(len(data1), 100)

            async with app.test_request_context(
                "/proxy/dash/BV1xx411c7xx/0/video/32/7",
                headers={"Range": "bytes=100-199", "X-Real-IP": "203.0.113.9"},
            ):
                resp4 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7)
                self.assertEqual(resp4.status_code, 206)

            # Clean up remaining streams
            for r in (resp2, resp_other, resp4):
                async with r.response as b:
                    _ = [c async for c in aiter(b)]

    async def test_manifest_validates_vid_and_throttles_fresh_bust(self):
        from unittest.mock import AsyncMock, patch

        import miku.dash.mpd as dash_mpd
        from miku.app import app

        sample_dash = {
            "dash": {
                "duration": 60,
                "minBufferTime": 1.5,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "bandwidth": 1000000,
                        "width": 1920,
                        "height": 1080,
                        "SegmentBase": {"Initialization": "0-999", "indexRange": "1000-1500"},
                    }
                ],
            }
        }
        load_mock = AsyncMock(return_value=sample_dash)
        refresh_mock = AsyncMock(return_value=sample_dash)

        with (
            patch.object(dash_mpd, "_load_dash_data", load_mock),
            patch.object(dash_mpd, "_refresh_dash_data", refresh_mock),
        ):
            # Invalid vid rejected with 400
            async with app.test_request_context("/video/dash/bad_vid/0/manifest.mpd"):
                resp_bad = await dash_mpd.video_dash_manifest_view("bad_vid", 0)
                self.assertEqual(resp_bad.status_code, 400)
            load_mock.assert_not_awaited()
            refresh_mock.assert_not_awaited()

            # First ?fresh=1 triggers _refresh_dash_data
            async with app.test_request_context("/video/dash/BV1xx411c7xx/0/manifest.mpd?fresh=1"):
                resp1 = await dash_mpd.video_dash_manifest_view("BV1xx411c7xx", 0)
                self.assertEqual(resp1.status_code, 200)
            self.assertEqual(refresh_mock.await_count, 1)
            self.assertEqual(load_mock.await_count, 0)

            # Immediate repeat ?fresh=1 within cooldown uses cached _load_dash_data instead of re-busting
            async with app.test_request_context("/video/dash/BV1xx411c7xx/0/manifest.mpd?fresh=1"):
                resp2 = await dash_mpd.video_dash_manifest_view("BV1xx411c7xx", 0)
                self.assertEqual(resp2.status_code, 200)
            self.assertEqual(refresh_mock.await_count, 1)
            self.assertEqual(load_mock.await_count, 1)


if __name__ == "__main__":
    unittest.main()
