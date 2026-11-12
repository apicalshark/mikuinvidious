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
"""Unit tests for cache write integrity, scan limits, and transaction bounds."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


class BoundedScan(unittest.IsolatedAsyncioTestCase):
    async def test_limits_empty_scan_rounds_and_finds_urls(self):
        import miku.media.proxy as proxy

        scan = AsyncMock(side_effect=[(n, []) for n in range(1, 9)])
        get = AsyncMock(return_value=None)
        with patch.object(proxy.appredis, "scan", scan), patch.object(proxy.appredis, "get", get):
            self.assertIsNone(await proxy._first_live_quality_url("123"))
        self.assertEqual(scan.await_count, 8)
        self.assertEqual(scan.await_args.kwargs, {"cursor": 7, "match": "miku_live_123_*", "count": 100})
        self.assertEqual(get.await_count, len(proxy.ScreenResolution))
        scan = AsyncMock(side_effect=[(2, []), (0, [b"key"])])
        with (
            patch.object(proxy.appredis, "scan", scan),
            patch.object(proxy.appredis, "get", AsyncMock(return_value="url")),
        ):
            self.assertEqual(await proxy._first_live_quality_url("123"), "url")
            self.assertEqual(scan.await_count, 2)

    async def test_known_quality_is_found_after_scan_budget(self):
        import miku.media.proxy as proxy

        scan = AsyncMock(side_effect=[(n, []) for n in range(1, 9)])
        get = AsyncMock(side_effect=lambda key: b"url" if key == "miku_live_123_250" else None)
        with patch.object(proxy.appredis, "scan", scan), patch.object(proxy.appredis, "get", get):
            self.assertEqual(await proxy._first_live_quality_url("123"), b"url")
        self.assertEqual(scan.await_count, 8)
        self.assertLessEqual(get.await_count, len(proxy.ScreenResolution))

    async def test_scan_result_cannot_trigger_unbounded_gets(self):
        import miku.media.proxy as proxy

        scan = AsyncMock(return_value=(0, [f"key{i}" for i in range(500)]))
        get = AsyncMock(return_value=None)
        with patch.object(proxy.appredis, "scan", scan), patch.object(proxy.appredis, "get", get):
            self.assertIsNone(await proxy._first_live_quality_url("123"))
        self.assertEqual(get.await_count, 100 + len(proxy.ScreenResolution))

    async def test_quality_write_keeps_default_in_same_transaction(self):
        import miku.views.live as views_live

        pipe = MagicMock()
        pipe.execute = AsyncMock()
        with patch.object(views_live.appredis, "pipeline", return_value=pipe) as pipeline:
            await views_live._cache_live_quality(123, 987, "quality-url", "default-url")
        pipeline.assert_called_once_with(transaction=True)
        self.assertEqual(
            [call.args for call in pipe.setex.call_args_list],
            [("miku_live_123_987", 1800, "quality-url"), ("miku_live_123", 1800, "default-url")],
        )
        pipe.execute.assert_awaited_once()


class CacheWrites(unittest.IsolatedAsyncioTestCase):
    async def test_danmaku_only_caches_xml_like_body(self):
        import miku.media.res as res
        from miku.app import app

        get_xml = AsyncMock(side_effect=["  upstream error", "  <i></i>"])
        cache_write = AsyncMock()
        with (
            patch.object(res.appredis, "get", AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", cache_write),
            patch.object(res.video.Video, "get_danmaku_xml", get_xml),
        ):
            client = app.test_client()
            await client.get("/res/danmaku/BV1xx411c7mD")
            cache_write.assert_not_awaited()
            await client.get("/res/danmaku/BV1xx411c7mD")
            cache_write.assert_awaited_once_with("miku_danmaku_xml_BV1xx411c7mD_0", res.DANMAKU_CACHE_TTL, "  <i></i>")

    async def test_nyaa_caches_only_present_results(self):
        import miku.views.bangumi as b

        bg = MagicMock()
        bg.get_meta = AsyncMock(return_value={"media": {"title": "Anime"}})
        torrent = SimpleNamespace(title="[CHS] Anime", magnet_url="magnet", seeders=1, leechers=0, size="1G")
        search = AsyncMock(return_value=[])
        cache = AsyncMock()
        with (
            patch.object(b.bangumi, "Bangumi", return_value=bg),
            patch.object(b, "search_nyaa", search),
            patch.object(b.shared, "render_template_with_theme", AsyncMock(return_value="sidebar")),
            patch.object(b.shared, "cache_set", cache),
        ):
            _, cacheable = await b._build_nyaa_payload(1, "nyaa:v2:1:modern:en")
            self.assertFalse(cacheable)
            cache.assert_not_awaited()
            search.return_value = [torrent]
            _, cacheable = await b._build_nyaa_payload(1, "nyaa:v2:1:modern:en")
            self.assertTrue(cacheable)
            self.assertEqual(cache.await_args.args[0], "nyaa:v2:1:modern:en")
            self.assertEqual(cache.await_args.args[2], b.NYAA_CACHE_TTL)

    async def test_listen_does_not_cache_fallback_pages(self):
        import miku.views.video as v
        from miku.app import app

        inst = MagicMock()
        inst.get_info = AsyncMock(return_value={"bvid": "BV1xx411c7mD", "title": "Video"})
        inst.get_tags = AsyncMock(return_value=[])
        inst.get_related = AsyncMock(return_value=[])
        inst.get_pages = AsyncMock(side_effect=[RuntimeError("offline"), [{"page": 1, "part": "real"}]])
        cache = AsyncMock()
        with (
            patch.object(v.video, "Video", return_value=inst),
            patch.object(v, "cache_minutes", return_value=15),
            patch.object(v, "cache_get", AsyncMock(return_value=None)),
            patch.object(v, "cache_set", cache),
            patch.object(v.appredis, "exists", AsyncMock(return_value=True)),
            patch.object(v.comment, "get_comments", AsyncMock(return_value={})),
            patch.object(v, "render_template_with_theme", AsyncMock(return_value="ok")),
        ):
            client = app.test_client()
            response = await client.get("/video_listen/BV1xx411c7mD")
            self.assertEqual(response.status_code, 200)
            cache.assert_not_awaited()
            await client.get("/video_listen/BV1xx411c7mD")
            self.assertEqual(cache.await_args.args[1]["vset"], [{"page": 1, "part": "real"}])

    async def test_listen_cache_requires_complete_upstream_lists(self):
        import miku.views.video as v
        from miku.app import app

        inst = MagicMock()
        inst.get_info = AsyncMock(return_value={"bvid": "BV1xx411c7mD"})
        inst.get_tags = AsyncMock(return_value=[])
        inst.get_related = AsyncMock(return_value=[])
        inst.get_pages = AsyncMock(return_value=[{"page": 1, "part": "real"}])
        cache = AsyncMock()
        with (
            patch.object(v.video, "Video", return_value=inst),
            patch.object(v, "cache_minutes", return_value=15),
            patch.object(v, "cache_get", AsyncMock(return_value=None)),
            patch.object(v, "cache_set", cache),
            patch.object(v.appredis, "exists", AsyncMock(return_value=True)),
            patch.object(v.comment, "get_comments", AsyncMock(return_value={})),
            patch.object(v, "render_template_with_theme", AsyncMock(return_value="ok")),
        ):
            client = app.test_client()
            for method, bad_value in (
                (inst.get_tags, RuntimeError("tags")),
                (inst.get_related, None),
                (inst.get_pages, []),
            ):
                old_value = method.return_value
                method.return_value = bad_value
                response = await client.get("/video_listen/BV1xx411c7mD")
                self.assertEqual(response.status_code, 200)
                cache.assert_not_awaited()
                method.return_value = old_value
            await client.get("/video_listen/BV1xx411c7mD")
            cache.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
