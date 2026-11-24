# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest

import miku.dash.mpd as dash_mpd
from miku.core.url_signing import sign_dash_manifest_url, sign_dash_track_url
from miku.dash.mpd import generate_vod_mpd


def _manifest_sig(vid: str, idx: int) -> str:
    return sign_dash_manifest_url(vid, idx).split("/")[4]


def _track_sig(vid: str, idx: int, media_type: str, qn: int, cid: int) -> str:
    return sign_dash_track_url(vid, idx, media_type, qn, cid).split("/")[4]


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
        self.assertIn(f"<BaseURL>{sign_dash_track_url('BV1xx411c7xx', 0, 'video', 80, 7)}</BaseURL>", mpd)
        self.assertIn(f"<BaseURL>{sign_dash_track_url('BV1xx411c7xx', 0, 'video', 80, 12)}</BaseURL>", mpd)
        self.assertIn(f"<BaseURL>{sign_dash_track_url('BV1xx411c7xx', 0, 'audio', 30280, 0)}</BaseURL>", mpd)
        self.assertIn('<Initialization range="0-999"/>', mpd)
        self.assertIn('<Initialization range="0-1024"/>', mpd)
        self.assertIn('<Initialization range="0-800"/>', mpd)

    def test_video_adaptation_sets_ordered_av1_hevc_avc(self):
        dash_data = {
            "dash": {
                "duration": 60,
                "minBufferTime": 1.5,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "bandwidth": 2000000,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "30",
                        "codecs": "avc1.640028",
                        "SegmentBase": {"Initialization": "0-100", "indexRange": "101-200"},
                    },
                    {
                        "id": 80,
                        "codecid": 12,
                        "bandwidth": 1500000,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "30",
                        "codecs": "hev1.1.6.L150.90",
                        "SegmentBase": {"Initialization": "0-100", "indexRange": "101-200"},
                    },
                    {
                        "id": 80,
                        "codecid": 13,
                        "bandwidth": 1200000,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "30",
                        "codecs": "av01.0.08M.08",
                        "SegmentBase": {"Initialization": "0-100", "indexRange": "101-200"},
                    },
                ],
                "audio": [],
            }
        }
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, dash_data)
        self.assertIsNotNone(mpd)
        pos_av1 = mpd.find("video_80_13")
        pos_hevc = mpd.find("video_80_12")
        pos_avc = mpd.find("video_80_7")
        self.assertTrue(pos_av1 != -1 and pos_hevc != -1 and pos_avc != -1)
        self.assertLess(pos_av1, pos_hevc)
        self.assertLess(pos_hevc, pos_avc)

        mpd_hevc = generate_vod_mpd("BV1xx411c7xx", 0, dash_data, preferred_codec="hevc")
        self.assertLess(mpd_hevc.find("video_80_12"), mpd_hevc.find("video_80_7"))
        self.assertLess(mpd_hevc.find("video_80_7"), mpd_hevc.find("video_80_13"))

        mpd_avc = generate_vod_mpd("BV1xx411c7xx", 0, dash_data, preferred_codec="avc")
        self.assertLess(mpd_avc.find("video_80_7"), mpd_avc.find("video_80_12"))
        self.assertLess(mpd_avc.find("video_80_12"), mpd_avc.find("video_80_13"))

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

    def test_xml_escaping_attributes_and_text(self):
        import xml.etree.ElementTree as ET

        dash_data = {
            "dash": {
                "duration": 10,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "codecs": "avc1\" onclick=\"alert('x&y<z>')",
                        "bandwidth": 1000,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "24",
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v.m4s",
                        "SegmentBase": {"Initialization": "0-100", "indexRange": "101-200"},
                    }
                ],
                "audio": [],
            }
        }
        mpd = generate_vod_mpd('BV1<x&y>"', 0, dash_data)
        self.assertIsNotNone(mpd)
        self.assertIn("&quot;", mpd)
        self.assertIn("&apos;", mpd)
        self.assertIn("&amp;", mpd)
        self.assertIn("&lt;", mpd)
        root = ET.fromstring(mpd)
        base_url = root.find(".//{urn:mpeg:dash:schema:mpd:2011}BaseURL")
        self.assertIsNotNone(base_url)
        self.assertIn('BV1<x&y>"', base_url.text)

    def test_omits_empty_adaptation_sets_with_sequential_ids(self):
        import xml.etree.ElementTree as ET

        dash_data = {
            "dash": {
                "duration": 10,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/no_sb.m4s",
                        # missing SegmentBase -> entire codec 7 AdaptationSet is empty
                    },
                    {
                        "id": 64,
                        "codecid": 12,
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/valid.m4s",
                        "SegmentBase": {"Initialization": "0-100", "indexRange": "101-200"},
                    },
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s",
                        "SegmentBase": {"Initialization": "0-50", "indexRange": "51-100"},
                    }
                ],
            }
        }
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, dash_data)
        self.assertIsNotNone(mpd)
        root = ET.fromstring(mpd)
        sets = root.findall(".//{urn:mpeg:dash:schema:mpd:2011}AdaptationSet")
        self.assertEqual(len(sets), 2)
        self.assertEqual([s.attrib["id"] for s in sets], ["1", "2"])
        self.assertEqual([s.attrib["contentType"] for s in sets], ["video", "audio"])

    def test_returns_none_when_zero_representations_exist(self):
        dash_data = {
            "dash": {
                "duration": 10,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/v.m4s",
                        "SegmentBase": {},
                    },
                ],
                "audio": [
                    {"id": 30280, "codecid": 0, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/a.m4s"},
                ],
            }
        }
        self.assertIsNone(generate_vod_mpd("BV1xx411c7xx", 0, dash_data))


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
        sig = _track_sig("BV1xx411c7xx", 0, "video", 80, 7)
        with patch.object(dash_stream, "_load_dash_data", load_mock):
            # 1. Missing Range header (crawler scraping BaseURL from MPD)
            async with app.test_request_context(sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7)):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig)
                self.assertEqual(resp.status_code, 416)
                self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")

            # 2. Open-ended Range: bytes=0-
            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": "bytes=0-"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig)
                self.assertEqual(resp.status_code, 416)

            # 3. Oversized Range (> DASH_MAX_FRAGMENT_BYTES)
            too_big = dash_stream.DASH_MAX_FRAGMENT_BYTES + 100
            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": f"bytes=0-{too_big}"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig)
                self.assertEqual(resp.status_code, 416)

            # 4. Inverted Range (end < start)
            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": "bytes=500-100"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig)
                self.assertEqual(resp.status_code, 416)

        load_mock.assert_not_awaited()

    async def test_proxy_dash_rejects_invalid_vid_or_params(self):
        from unittest.mock import AsyncMock, patch

        import miku.dash.stream as dash_stream
        from miku.app import app

        load_mock = AsyncMock()
        with patch.object(dash_stream, "_load_dash_data", load_mock):
            bad_sig = _track_sig("not_a_valid_vid", 0, "video", 80, 7)
            async with app.test_request_context(
                sign_dash_track_url("not_a_valid_vid", 0, "video", 80, 7),
                headers={"Range": "bytes=0-999"},
            ):
                resp = await dash_stream.proxy_dash("not_a_valid_vid", 0, "video", 80, 7, sig=bad_sig)
                self.assertEqual(resp.status_code, 400)

            neg_idx_sig = _track_sig("BV1xx411c7xx", -1, "video", 80, 7)
            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", -1, "video", 80, 7),
                headers={"Range": "bytes=0-999"},
            ):
                resp = await dash_stream.proxy_dash("BV1xx411c7xx", -1, "video", 80, 7, sig=neg_idx_sig)
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
            sig80 = _track_sig("BV1xx411c7xx", 0, "video", 80, 7)
            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": "bytes=0-99", "X-Real-IP": "203.0.113.9"},
            ):
                resp1 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig80)
                self.assertEqual(resp1.status_code, 206)

            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": "bytes=0-99", "X-Real-IP": "203.0.113.9"},
            ):
                resp2 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig80)
                self.assertEqual(resp2.status_code, 206)

            # 3rd concurrent stream from the same IP is rejected with 429
            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": "bytes=0-99", "X-Real-IP": "203.0.113.9"},
            ):
                resp3 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig80)
                self.assertEqual(resp3.status_code, 429)
                self.assertEqual(resp3.headers.get("Retry-After"), "1")

            # Another IP can still stream normally
            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": "bytes=0-99", "X-Real-IP": "198.51.100.7"},
            ):
                resp_other = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig80)
                self.assertEqual(resp_other.status_code, 206)

            # Consume resp1 to completion -> frees 1 slot for 203.0.113.9
            gate.set()
            async with resp1.response as body1:
                data1 = b"".join([c async for c in aiter(body1)])
            self.assertEqual(len(data1), 100)

            async with app.test_request_context(
                sign_dash_track_url("BV1xx411c7xx", 0, "video", 80, 7),
                headers={"Range": "bytes=100-199", "X-Real-IP": "203.0.113.9"},
            ):
                resp4 = await dash_stream.proxy_dash("BV1xx411c7xx", 0, "video", 80, 7, sig=sig80)
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
            bad_sig = _manifest_sig("bad_vid", 0)
            async with app.test_request_context(sign_dash_manifest_url("bad_vid", 0)):
                resp_bad = await dash_mpd.video_dash_manifest_view("bad_vid", 0, sig=bad_sig)
                self.assertEqual(resp_bad.status_code, 400)
            load_mock.assert_not_awaited()
            refresh_mock.assert_not_awaited()

            # First ?fresh=1 triggers _refresh_dash_data
            good_sig = _manifest_sig("BV1xx411c7xx", 0)
            signed_fresh_url = f"{sign_dash_manifest_url('BV1xx411c7xx', 0)}?fresh=1"
            async with app.test_request_context(signed_fresh_url):
                resp1 = await dash_mpd.video_dash_manifest_view("BV1xx411c7xx", 0, sig=good_sig)
                self.assertEqual(resp1.status_code, 200)
            self.assertEqual(refresh_mock.await_count, 1)
            self.assertEqual(load_mock.await_count, 0)

            # Immediate repeat ?fresh=1 within cooldown uses cached _load_dash_data instead of re-busting
            async with app.test_request_context(signed_fresh_url):
                resp2 = await dash_mpd.video_dash_manifest_view("BV1xx411c7xx", 0, sig=good_sig)
                self.assertEqual(resp2.status_code, 200)
            self.assertEqual(refresh_mock.await_count, 1)
            self.assertEqual(load_mock.await_count, 1)

            # ?audio_only=1 serves audio-only MPD
            sample_dash["dash"]["audio"] = [
                {
                    "id": 30280,
                    "codecid": 0,
                    "bandwidth": 320000,
                    "codecs": "mp4a.40.2",
                    "SegmentBase": {"Initialization": "0-800", "indexRange": "801-1200"},
                }
            ]
            audio_only_url = sign_dash_manifest_url("BV1xx411c7xx", 0, audio_only=True)
            async with app.test_request_context(audio_only_url):
                resp_audio = await dash_mpd.video_dash_manifest_view("BV1xx411c7xx", 0, sig=good_sig)
                self.assertEqual(resp_audio.status_code, 200)
                body_audio = await resp_audio.get_data(as_text=True)
                self.assertIn('contentType="audio"', body_audio)
                self.assertNotIn('contentType="video"', body_audio)

    async def test_unsigned_and_tampered_dash_routes_return_403(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        import miku.core.rate_limit as rate_limit
        import miku.dash.stream as dash_stream
        from miku.app import app

        load_mock = AsyncMock(return_value=None)
        refresh_mock = AsyncMock(return_value=None)
        limiter = MagicMock()
        limiter.is_allowed = AsyncMock(return_value=(False, {"remaining": 0, "reset": 1, "limit": 10}))
        client = app.test_client()
        with (
            patch.dict(rate_limit.appconf["rate_limit"], {"enabled": True}),
            patch.object(rate_limit, "get_rate_limiter", return_value=limiter),
            patch.object(dash_mpd, "_load_dash_data", load_mock),
            patch.object(dash_mpd, "_refresh_dash_data", refresh_mock),
            patch.object(dash_stream, "_load_dash_data", load_mock),
        ):
            for path in (
                "/video/dash/BV1xx411c7mD/0/manifest.mpd",
                "/video/dash/BV1xx411c7mD/0/manifest.mpd?fresh=1",
                "/video/dash/s/0000000000000000/BV1xx411c7mD/0/manifest.mpd",
                "/proxy/dash/BV1xx411c7mD/0/video/80/7",
                "/proxy/dash/s/0000000000000000/BV1xx411c7mD/0/video/80/7",
                sign_dash_track_url("BV1xx411c7mD", 0, "video", 80, 7).replace("/video/80/7", "/video/64/7"),
            ):
                resp = await client.get(path)
                self.assertEqual(resp.status_code, 403, path)
        load_mock.assert_not_awaited()
        refresh_mock.assert_not_awaited()
        limiter.is_allowed.assert_not_awaited()

    def test_invalid_track_numeric_fields_skipped_without_failing_siblings(self):
        dash_data = {
            "dash": {
                "duration": 60,
                "minBufferTime": 1.5,
                "video": [
                    {
                        "id": 80,
                        "codecid": None,
                        "bandwidth": 1500000,
                        "SegmentBase": {"Initialization": "0-999", "indexRange": "1000-1500"},
                    },
                    {
                        "id": 64,
                        "codecid": 7,
                        "bandwidth": 900000,
                        "codecs": "avc1.64001F",
                        "SegmentBase": {"Initialization": "0-888", "indexRange": "889-1200"},
                    },
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": None,
                        "bandwidth": 320000,
                        "SegmentBase": {"Initialization": "0-700", "indexRange": "701-1000"},
                    },
                    {
                        "id": 30232,
                        "codecid": 0,
                        "bandwidth": 128000,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {"Initialization": "0-600", "indexRange": "601-900"},
                    },
                ],
            }
        }
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, dash_data)
        self.assertIsNotNone(mpd)
        self.assertIn(sign_dash_track_url("BV1xx411c7xx", 0, "video", 64, 7), mpd)
        self.assertIn(sign_dash_track_url("BV1xx411c7xx", 0, "audio", 30232, 0), mpd)
        self.assertNotIn("video_80_", mpd)
        self.assertNotIn("audio_30280_", mpd)

    def test_mpd_audio_only_omits_video_adaptation_sets(self):
        import xml.etree.ElementTree as ET

        sample_dash = {
            "dash": {
                "duration": 60,
                "minBufferTime": 1.5,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "bandwidth": 1500000,
                        "codecs": "avc1.640028",
                        "SegmentBase": {"Initialization": "0-999", "indexRange": "1000-1500"},
                    }
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 320000,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {"Initialization": "0-800", "indexRange": "801-1200"},
                    }
                ],
            }
        }
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, sample_dash, audio_only=True)
        self.assertIsNotNone(mpd)
        root = ET.fromstring(mpd)
        sets = root.findall(".//{urn:mpeg:dash:schema:mpd:2011}AdaptationSet")
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].attrib["contentType"], "audio")
        self.assertEqual(sets[0].attrib["id"], "1")
        self.assertNotIn("video_80_", mpd)
        self.assertIn("audio_30280_0", mpd)


if __name__ == "__main__":
    unittest.main()
