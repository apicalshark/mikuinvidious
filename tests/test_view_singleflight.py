# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.views.audio as views_audio
import miku.views.bangumi as views_bangumi
import miku.views.home as views_home
import miku.views.space as views_space
import miku.views.video as views_video
from miku.app import app


class _SlowGate:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()


class TestViewSingleflight(unittest.IsolatedAsyncioTestCase):
    async def test_home_view_coalesces_concurrent_cold_misses(self):
        gate = _SlowGate()

        async def fake_get_videos():
            gate.calls += 1
            gate.started.set()
            await gate.release.wait()
            return {"item": [{"bvid": "BV1xx411c7mD", "title": "Home Video", "owner": {"name": "Up", "mid": 1}}]}

        with (
            patch.object(views_home, "cache_minutes", return_value=0),
            patch.object(views_home.homepage, "get_videos", side_effect=fake_get_videos),
            patch.object(views_home, "render_template_with_theme", AsyncMock(return_value="<html>home</html>")),
        ):

            async def hit():
                async with app.test_request_context("/"):
                    return await views_home.home_view()

            tasks = [asyncio.create_task(hit()) for _ in range(5)]
            await gate.started.wait()
            gate.release.set()
            results = await asyncio.gather(*tasks)

        self.assertEqual(gate.calls, 1)
        for res in results:
            body = res if isinstance(res, str) else await res.get_data(as_text=True)
            self.assertIn("home", body)

    async def test_video_view_coalesces_cold_misses_and_never_neg_caches_404(self):
        gate = _SlowGate()
        fake_vinfo = {
            "bvid": "BV1xx411c7mD",
            "title": "Test Video",
            "pic": "https://i0.hdslb.com/bfs/archive/a.jpg",
            "desc": "desc",
            "owner": {"name": "Up", "mid": 1},
            "stat": {"view": 1, "like": 1, "coin": 1, "favorite": 1, "share": 1, "danmaku": 1},
            "pubdate": 1700000000,
        }

        async def fake_get_info():
            gate.calls += 1
            gate.started.set()
            await gate.release.wait()
            return dict(fake_vinfo)

        fake_video = MagicMock()
        fake_video.get_info = AsyncMock(side_effect=fake_get_info)
        fake_video.get_tags = AsyncMock(return_value=[])
        fake_video.get_related = AsyncMock(return_value=[])
        fake_video.get_pages = AsyncMock(return_value=[{"page": 1, "part": "P1", "cid": 100}])

        with (
            patch.object(views_video, "cache_minutes", return_value=0),
            patch.object(views_video.video, "Video", return_value=fake_video),
            patch.object(views_video, "_fetch_owner_meta", AsyncMock(return_value={})),
            patch.object(views_video, "render_template_with_theme", AsyncMock(return_value="<html>video</html>")),
            patch("miku.dash.video_get_dash_for_qn", AsyncMock(return_value=None)),
        ):

            async def hit():
                async with app.test_request_context("/video/BV1xx411c7mD"):
                    return await views_video.video_view("BV1xx411c7mD")

            tasks = [asyncio.create_task(hit()) for _ in range(5)]
            await gate.started.wait()
            gate.release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(gate.calls, 1)

        seq_video = MagicMock()
        seq_video.get_info = AsyncMock(side_effect=[RuntimeError("啥都木有 -404"), dict(fake_vinfo)])
        seq_video.get_tags = AsyncMock(return_value=[])
        seq_video.get_related = AsyncMock(return_value=[])
        seq_video.get_pages = AsyncMock(return_value=[{"page": 1, "part": "P1", "cid": 100}])

        with (
            patch.object(views_video, "cache_minutes", return_value=0),
            patch.object(views_video.video, "Video", return_value=seq_video),
            patch.object(views_video, "_fetch_owner_meta", AsyncMock(return_value={})),
            patch.object(views_video, "render_template_with_theme", AsyncMock(return_value="<html>video</html>")),
            patch("miku.dash.video_get_dash_for_qn", AsyncMock(return_value=None)),
        ):
            async with app.test_request_context("/video/BV1xx411c7xx"):
                first = await views_video.video_view("BV1xx411c7xx")
            async with app.test_request_context("/video/BV1xx411c7xx"):
                second = await views_video.video_view("BV1xx411c7xx")

        self.assertIn(first[1], (404, 500))
        self.assertNotIsInstance(second, tuple)
        self.assertEqual(seq_video.get_info.await_count, 2)

    async def test_space_and_author_views_coalesce_cold_misses(self):
        space_gate = _SlowGate()

        async def fake_uinfo():
            space_gate.calls += 1
            space_gate.started.set()
            await space_gate.release.wait()
            return {"mid": 12345, "name": "Creator", "face": "https://i0.hdslb.com/f.jpg", "sign": "hi"}

        fake_user = MagicMock()
        fake_user._degraded = False
        fake_user._videos_load_failed = False
        fake_user.get_user_info = AsyncMock(side_effect=fake_uinfo)
        fake_user.get_videos = AsyncMock(return_value={"list": {"vlist": []}, "page": {"count": 0, "pn": 1, "ps": 28}})

        with (
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space.user, "User", return_value=fake_user),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>space</html>")),
        ):

            async def hit_space():
                async with app.test_request_context("/space/12345"):
                    return await views_space.space_view("12345")

            tasks = [asyncio.create_task(hit_space()) for _ in range(5)]
            await space_gate.started.wait()
            space_gate.release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(space_gate.calls, 1)

        author_gate = _SlowGate()

        async def fake_articles(**kwargs):
            author_gate.calls += 1
            author_gate.started.set()
            await author_gate.release.wait()
            return {"articles": []}

        author_user = MagicMock()
        author_user.get_user_info = AsyncMock(return_value={"mid": 12345, "name": "Creator"})
        author_user.get_articles = AsyncMock(side_effect=fake_articles)
        author_user.get_opus_articles = AsyncMock(return_value={"items": []})

        with (
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space.user, "User", return_value=author_user),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>author</html>")),
        ):

            async def hit_author():
                async with app.test_request_context("/author/12345"):
                    return await views_space.author_view("12345")

            tasks = [asyncio.create_task(hit_author()) for _ in range(5)]
            await author_gate.started.wait()
            author_gate.release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(author_gate.calls, 1)

    async def test_read_view_coalesces_scrapes_and_negative_caches_only_404(self):
        gate = _SlowGate()
        opus_html = (
            "<html><head><title>Opus</title></head><body>"
            "<div class='opus-module-content'><p>Article paragraph text for testing.</p></div>"
            "</body></html>"
        )

        class _Resp:
            def __init__(self, status_code, text):
                self.status_code = status_code
                self.text = text
                self.url = "https://www.bilibili.com/opus/987654321"

            async def aclose(self):
                return None

        async def fake_send(req, **kwargs):
            gate.calls += 1
            gate.started.set()
            await gate.release.wait()
            return _Resp(200, opus_html)

        fake_client = MagicMock()
        fake_client.build_request = MagicMock(return_value=object())
        fake_client.send = AsyncMock(side_effect=fake_send)

        fake_opus = MagicMock()
        fake_opus.get_info = AsyncMock(return_value={"item": {"modules": []}})

        with (
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "b"})),
            patch.object(views_space.Network, "get_async_client", AsyncMock(return_value=fake_client)),
            patch.object(views_space, "article_to_html", return_value="<p>Article paragraph text.</p>"),
            patch.object(views_space, "get_article_info", return_value={"title": "Opus", "author": {"name": "A"}}),
            patch.object(views_space.opus, "Opus", return_value=fake_opus),
            patch.object(
                views_space,
                "_fetch_article_comments",
                AsyncMock(return_value=({"page": {"count": 0}, "replies": []}, "987654321", 11, False)),
            ),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>opus</html>")),
        ):

            async def hit_opus():
                async with app.test_request_context("/opus/987654321"):
                    return await views_space.read_view("987654321")

            tasks = [asyncio.create_task(hit_opus()) for _ in range(5)]
            await gate.started.wait()
            gate.release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(gate.calls, 1)

        if hasattr(views_space, "_article_neg_cache"):
            views_space._article_neg_cache.clear()

        not_found_client = MagicMock()
        not_found_client.build_request = MagicMock(return_value=object())
        not_found_client.send = AsyncMock(return_value=_Resp(404, "not found"))

        with (
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "b"})),
            patch.object(views_space.Network, "get_async_client", AsyncMock(return_value=not_found_client)),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>err</html>")),
        ):
            async with app.test_request_context("/opus/404404"):
                r1 = await views_space.read_view("404404")
            async with app.test_request_context("/opus/404404"):
                r2 = await views_space.read_view("404404")

        self.assertEqual(r1[1], 404)
        self.assertEqual(r2[1], 404)
        self.assertEqual(not_found_client.send.await_count, 1)

        error_client = MagicMock()
        error_client.build_request = MagicMock(return_value=object())
        error_client.send = AsyncMock(return_value=_Resp(500, "err"))

        with (
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "b"})),
            patch.object(views_space.Network, "get_async_client", AsyncMock(return_value=error_client)),
            patch.object(views_space.asyncio, "sleep", AsyncMock()),
            patch.object(views_space.opus, "Opus", return_value=MagicMock(get_info=AsyncMock(return_value={}))),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>err</html>")),
        ):
            async with app.test_request_context("/opus/502502"):
                e1 = await views_space.read_view("502502")
            async with app.test_request_context("/opus/502502"):
                e2 = await views_space.read_view("502502")

        self.assertEqual(e1[1], 502)
        self.assertEqual(e2[1], 502)
        self.assertEqual(error_client.send.await_count, 10)

    async def test_bangumi_view_coalesces_concurrent_cold_misses(self):
        gate = _SlowGate()

        async def fake_meta():
            gate.calls += 1
            gate.started.set()
            await gate.release.wait()
            return {"media": {"title": "Bangumi", "episodes": [{"title": "1", "bvid": "BV1xx411c7mD", "id": 1}]}}

        fake_b = MagicMock()
        fake_b.get_meta = AsyncMock(side_effect=fake_meta)
        fake_b.get_episode_list = AsyncMock(return_value={})

        with (
            patch.object(views_bangumi.shared, "cache_minutes", return_value=0),
            patch.object(views_bangumi.bangumi, "Bangumi", return_value=fake_b),
            patch.object(views_bangumi.shared, "render_template_with_theme", AsyncMock(return_value="<html>bg</html>")),
        ):

            async def hit():
                async with app.test_request_context("/bangumi/view/12345"):
                    return await views_bangumi.bangumi_view(12345)

            tasks = [asyncio.create_task(hit()) for _ in range(5)]
            await gate.started.wait()
            gate.release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(gate.calls, 1)

    async def test_audio_and_audiolist_views_coalesce_concurrent_cold_misses(self):
        au_gate = _SlowGate()

        async def fake_au_info():
            au_gate.calls += 1
            au_gate.started.set()
            await au_gate.release.wait()
            return {"title": "Song", "cover": "", "intro": "", "author": "Singer", "mid": 1, "statistic": {}}

        fake_au = MagicMock()
        fake_au.get_info = AsyncMock(side_effect=fake_au_info)
        fake_au.get_download_url = AsyncMock(return_value={"cdns": ["https://example.com/a.mp3"]})

        with (
            patch.object(views_audio, "cache_minutes", return_value=0),
            patch.object(views_audio.audio, "Audio", return_value=fake_au),
            patch.object(
                views_audio.comment, "get_comments", AsyncMock(return_value={"page": {"count": 0}, "replies": []})
            ),
            patch.object(views_audio.appredis, "exists", AsyncMock(return_value=True)),
            patch.object(views_audio, "render_template_with_theme", AsyncMock(return_value="<html>au</html>")),
        ):

            async def hit_au():
                async with app.test_request_context("/audio/au12345"):
                    return await views_audio.audio_view("au12345")

            tasks = [asyncio.create_task(hit_au()) for _ in range(5)]
            await au_gate.started.wait()
            au_gate.release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(au_gate.calls, 1)

        al_gate = _SlowGate()

        async def fake_song_list():
            al_gate.calls += 1
            al_gate.started.set()
            await al_gate.release.wait()
            return {"data": [{"id": 999, "title": "Track 1", "duration": 100}]}

        fake_al = MagicMock()
        fake_al.get_song_list = AsyncMock(side_effect=fake_song_list)
        fake_al.get_info = AsyncMock(return_value={"title": "List"})

        fake_track = MagicMock()
        fake_track.get_info = AsyncMock(
            return_value={"title": "Track 1", "cover": "", "intro": "", "author": "Singer", "mid": 1, "statistic": {}}
        )
        fake_track.get_download_url = AsyncMock(return_value={"cdns": []})

        with (
            patch.object(views_audio, "cache_minutes", return_value=0),
            patch.object(views_audio.audio, "AudioList", return_value=fake_al),
            patch.object(views_audio.audio, "Audio", return_value=fake_track),
            patch.object(
                views_audio.comment, "get_comments", AsyncMock(return_value={"page": {"count": 0}, "replies": []})
            ),
            patch.object(views_audio.appredis, "exists", AsyncMock(return_value=True)),
            patch.object(views_audio, "render_template_with_theme", AsyncMock(return_value="<html>al</html>")),
        ):

            async def hit_al():
                async with app.test_request_context("/audio_list/am12345"):
                    return await views_audio.audio_list_view("am12345")

            tasks = [asyncio.create_task(hit_al()) for _ in range(5)]
            await al_gate.started.wait()
            al_gate.release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(al_gate.calls, 1)
