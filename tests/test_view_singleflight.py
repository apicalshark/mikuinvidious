# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.core.rate_limit as rate_limit
import miku.views.audio as views_audio
import miku.views.bangumi as views_bangumi
import miku.views.home as views_home
import miku.views.space as views_space
import miku.views.video as views_video
from miku.api.exceptions import ResponseCodeException
from miku.app import app


class _SlowGate:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()


class TestViewSingleflight(unittest.IsolatedAsyncioTestCase):
    async def _run_coalesced(self, coros, gate: _SlowGate, *, sf=None, sf_key: str | None = None):
        with patch.dict(rate_limit.appconf["rate_limit"], {"enabled": False}):
            tasks = [asyncio.create_task(c) for c in coros]
            wait_started = asyncio.create_task(gate.started.wait())
            try:
                done, _pending = await asyncio.wait(
                    [*tasks, wait_started],
                    timeout=2.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wait_started not in done:
                    self.fail("Loader never reached gate.started before request tasks completed or timed out")
                if sf is not None and sf_key is not None:
                    loop = asyncio.get_running_loop()
                    expected_waiters = len(tasks)

                    async def _wait_followers():
                        while True:
                            entry = sf._loops.get(loop, {}).get(sf_key)
                            if entry is not None and entry.waiters >= expected_waiters:
                                return
                            await asyncio.sleep(0)

                    await asyncio.wait_for(_wait_followers(), timeout=2.0)
                gate.release.set()
                return await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)
            except BaseException:
                gate.release.set()
                wait_started.cancel()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, wait_started, return_exceptions=True)
                raise

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

            results = await self._run_coalesced(
                [hit() for _ in range(5)], gate, sf=views_home._home_sf, sf_key="home:data"
            )

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

            await self._run_coalesced(
                [hit() for _ in range(5)], gate, sf=views_video._video_sf, sf_key="BV1xx411c7mD:0"
            )

        self.assertEqual(gate.calls, 1)

        seq_video = MagicMock()
        seq_video.get_info = AsyncMock(side_effect=[ResponseCodeException(-404, "啥都木有"), dict(fake_vinfo)])
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

        self.assertEqual(first[1], 404)
        self.assertNotIsInstance(second, tuple)
        self.assertEqual(seq_video.get_info.await_count, 2)

    async def test_video_view_never_caches_fallback_vset_and_recovers_fresh_cache_on_error(self):
        fake_vinfo = {"bvid": "BV1xx411c7mD", "title": "Multi-part", "owner": {"mid": 1, "name": "Up"}}
        timeout_video = MagicMock()
        timeout_video.get_info = AsyncMock(return_value=dict(fake_vinfo))
        timeout_video.get_tags = AsyncMock(return_value=[])
        timeout_video.get_related = AsyncMock(return_value=[])
        timeout_video.get_pages = AsyncMock(side_effect=TimeoutError("pages timeout"))

        cache_set_mock = AsyncMock()
        with (
            patch.object(views_video, "cache_minutes", return_value=10),
            patch.object(views_video, "cache_get", AsyncMock(return_value=None)),
            patch.object(views_video, "cache_set", cache_set_mock),
            patch.object(views_video.video, "Video", return_value=timeout_video),
            patch.object(views_video, "_fetch_owner_meta", AsyncMock(return_value={})),
            patch.object(views_video, "render_template_with_theme", AsyncMock(return_value="<html>ok</html>")),
            patch("miku.dash.video_get_dash_for_qn", AsyncMock(return_value=None)),
        ):
            async with app.test_request_context("/video/BV1xx411c7mD"):
                resp = await views_video.video_view("BV1xx411c7mD")
        self.assertEqual(resp.status_code, 200)
        cache_set_mock.assert_not_awaited()

        fresh_cached = {
            "vinfo": dict(fake_vinfo),
            "vtags": [],
            "vrelated": [],
            "vset": [{"page": 1, "part": "P1"}, {"page": 2, "part": "P2"}],
        }
        failing_video = MagicMock()
        failing_video.get_info = AsyncMock(side_effect=RuntimeError("boom"))
        failing_video.get_tags = AsyncMock(return_value=[])
        failing_video.get_related = AsyncMock(return_value=[])
        failing_video.get_pages = AsyncMock(return_value=[])
        with (
            patch.object(views_video, "cache_minutes", return_value=10),
            patch.object(views_video, "cache_get", AsyncMock(return_value=None)),
            patch.object(views_video, "cache_get_stale", AsyncMock(return_value=(fresh_cached, False))),
            patch.object(views_video.video, "Video", return_value=failing_video),
            patch.object(views_video, "_fetch_owner_meta", AsyncMock(return_value={})),
            patch.object(views_video, "render_template_with_theme", AsyncMock(return_value="<html>hit</html>")),
        ):
            async with app.test_request_context("/video/BV1xx411c7mD"):
                recovered = await views_video.video_view("BV1xx411c7mD")
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.headers.get("X-Cache"), "HIT")

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
        fake_user.get_videos = AsyncMock(
            return_value={
                "list": {"vlist": [{"bvid": "BV1xx411c7mD", "title": "V"}]},
                "page": {"count": 1, "pn": 1, "ps": 30},
            }
        )

        with (
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space.user, "User", return_value=fake_user),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>space</html>")),
        ):

            async def hit_space():
                async with app.test_request_context("/space/12345"):
                    return await views_space.space_view("12345")

            async def hit_space_json():
                async with app.test_request_context("/space/12345/json"):
                    return await views_space.space_json_feed("12345")

            await self._run_coalesced(
                [hit_space(), hit_space(), hit_space_json()],
                space_gate,
                sf=views_space._space_sf,
                sf_key="12345:1",
            )

        self.assertEqual(space_gate.calls, 1)

        retry_gate = _SlowGate()
        retry_calls = 0

        async def fail_then_recover_uinfo():
            nonlocal retry_calls
            retry_calls += 1
            if retry_calls == 1:
                retry_gate.calls += 1
                retry_gate.started.set()
                await retry_gate.release.wait()
                raise RuntimeError("transient initial failure")
            await asyncio.sleep(0)
            return {"mid": 54321, "name": "Recovered", "face": "https://i0.hdslb.com/r.jpg", "sign": ""}

        retry_user = MagicMock()
        retry_user._degraded = False
        retry_user._videos_load_failed = False
        retry_user.get_user_info = AsyncMock(side_effect=fail_then_recover_uinfo)
        retry_user.get_videos = AsyncMock(return_value={"list": {"vlist": []}, "page": {"count": 0, "pn": 1, "ps": 30}})
        with (
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space.user, "User", return_value=retry_user),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>recovered</html>")),
        ):

            async def hit_retry_space():
                async with app.test_request_context("/space/54321"):
                    return await views_space.space_view("54321")

            retry_responses = await self._run_coalesced(
                [hit_retry_space() for _ in range(5)],
                retry_gate,
                sf=views_space._space_sf,
                sf_key="54321:1",
            )

        self.assertEqual(retry_calls, 2)
        self.assertTrue(all(r.status_code == 200 for r in retry_responses))

        json_fail_user = MagicMock()
        json_fail_user._degraded = False
        json_fail_user._videos_load_failed = False
        json_fail_user.get_user_info = AsyncMock(side_effect=RuntimeError("profile unavailable"))
        json_fail_user.get_videos = AsyncMock(
            return_value={"list": {"vlist": []}, "page": {"count": 0, "pn": 1, "ps": 30}}
        )
        with (
            patch.dict(rate_limit.appconf["rate_limit"], {"enabled": False}),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space.user, "User", return_value=json_fail_user),
        ):
            async with app.test_request_context("/space/98765/json"):
                json_fail_resp = await views_space.space_json_feed("98765")

        self.assertEqual(json_fail_resp.status_code, 502)
        self.assertEqual(json_fail_user.get_user_info.await_count, 1)

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

            await self._run_coalesced(
                [hit_author() for _ in range(5)], author_gate, sf=views_space._author_sf, sf_key="12345:1"
            )

        self.assertEqual(author_gate.calls, 1)

    async def test_empty_space_profile_recovery_is_html_only_and_coalesced(self):
        for missing in (None, {}):
            with self.subTest(missing=missing):
                gate = _SlowGate()

                async def recover(mid, gate=gate):
                    gate.calls += 1
                    gate.started.set()
                    await gate.release.wait()
                    return {"mid": 123, "name": "Recovered"}

                with (
                    patch.object(views_space, "cache_minutes", return_value=0),
                    patch.object(
                        views_space,
                        "_load_space_page1_shared",
                        AsyncMock(return_value=(missing, {"list": {"vlist": []}}, False, False)),
                    ),
                    patch.object(views_space, "_recover_space_profile", side_effect=recover),
                    patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="ok")),
                ):

                    async def html():
                        async with app.test_request_context("/space/123"):
                            return await views_space.space_view("123")

                    responses = await self._run_coalesced(
                        [html() for _ in range(5)], gate, sf=views_space._space_profile_sf, sf_key="123"
                    )
                    self.assertTrue(all(r.status_code == 200 for r in responses))
                    self.assertEqual(gate.calls, 1)
                    async with app.test_request_context("/space/123/json"):
                        await views_space.space_json_feed("123")
                    self.assertEqual(gate.calls, 1)

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

            await self._run_coalesced(
                [hit_opus() for _ in range(5)], gate, sf=views_space._article_sf, sf_key="opus:987654321"
            )

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

        with (
            patch.dict(views_space.appconf["render"], {"use_pandoc": True, "article_allowed_formats": ["txt"]}),
            patch.object(views_space, "cache_minutes", return_value=0),
            patch.object(views_space, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "b"})),
            patch.object(views_space.Network, "get_async_client", AsyncMock(return_value=not_found_client)),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>err</html>")),
        ):
            async with app.test_request_context("/opus/404405?format=txt"):
                exp_404 = await views_space.read_view("404405")
        self.assertEqual(exp_404[1], 404)
        self.assertIsNone(views_space._article_neg_cache.get("opus:404405"))

        views_space._article_neg_cache.set("cv:777", 404)
        with (
            patch.object(views_space, "cache_minutes", return_value=10),
            patch.object(views_space, "cache_get", AsyncMock(return_value={"opus_id": "888888"})),
        ):
            async with app.test_request_context("/read/cv777"):
                redir = await views_space.read_view("cv777")
        self.assertEqual(redir.status_code, 302)
        self.assertIn("/opus/888888", redir.headers.get("Location", ""))

        mobile_cached = {
            "arinfo": {"title": "Mobile", "author": {"mid": 1, "name": "U", "face": ""}},
            "content": "<p>ok</p>",
        }

        async def alias_cache_get(k, ttl):
            if k == "read:data:v2:123456:cv":
                return mobile_cached
            return None

        mobile_client = MagicMock()
        mobile_client.build_request = MagicMock(return_value=object())
        mobile_client.send = AsyncMock(return_value=_Resp(500, "err"))
        with (
            patch.object(views_space, "cache_minutes", return_value=10),
            patch.object(views_space, "cache_get", side_effect=alias_cache_get),
            patch.object(views_space, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "b"})),
            patch.object(views_space, "get_wbi_mixin_key", AsyncMock(return_value="")),
            patch.object(views_space.Network, "get_async_client", AsyncMock(return_value=mobile_client)),
            patch.object(views_space.asyncio, "sleep", AsyncMock()),
            patch.object(views_space, "_fetch_article_comments", AsyncMock(return_value=({}, "123", 12, False))),
            patch.object(views_space, "render_template_with_theme", AsyncMock(return_value="<html>mobile</html>")),
        ):
            async with app.test_request_context("/read/mobile/123456"):
                m_resp = await views_space.read_view("123456")
            async with app.test_request_context("/read/cv123456"):
                c_resp = await views_space.read_view("cv123456")
        self.assertEqual(m_resp.status_code, 200)
        self.assertEqual(c_resp[1], 502)

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

            await self._run_coalesced([hit() for _ in range(5)], gate, sf=views_bangumi._bangumi_sf, sf_key="12345")

        self.assertEqual(gate.calls, 1)

    async def test_audio_and_audiolist_views_coalesce_concurrent_cold_misses(self):
        au_gate = _SlowGate()
        comment_started = asyncio.Event()
        comment_proceed = asyncio.Event()
        expected_comments = {"page": {"count": 1}, "replies": [{"rpid": 1}]}

        async def fake_au_info():
            au_gate.calls += 1
            au_gate.started.set()
            await asyncio.wait_for(comment_started.wait(), timeout=1.0)
            comment_proceed.set()
            await au_gate.release.wait()
            return {"title": "Song", "cover": "", "intro": "", "author": "Singer", "mid": 1, "statistic": {}}

        async def fake_au_comments(*args, **kwargs):
            comment_started.set()
            await asyncio.wait_for(comment_proceed.wait(), timeout=1.0)
            return expected_comments

        fake_au = MagicMock()
        fake_au.get_info = AsyncMock(side_effect=fake_au_info)
        fake_au.get_download_url = AsyncMock(return_value={"cdns": ["https://example.com/a.mp3"]})
        render_au_mock = AsyncMock(return_value="<html>au</html>")

        with (
            patch.object(views_audio, "cache_minutes", return_value=0),
            patch.object(views_audio.audio, "Audio", return_value=fake_au),
            patch.object(views_audio.comment, "get_comments", side_effect=fake_au_comments),
            patch.object(views_audio.appredis, "exists", AsyncMock(return_value=True)),
            patch.object(views_audio, "render_template_with_theme", render_au_mock),
        ):

            async def hit_au():
                async with app.test_request_context("/audio/au12345"):
                    return await views_audio.audio_view("au12345")

            await self._run_coalesced([hit_au() for _ in range(5)], au_gate, sf=views_audio._audio_sf, sf_key="au12345")

        self.assertTrue(comment_started.is_set())
        self.assertEqual(au_gate.calls, 1)
        self.assertEqual(render_au_mock.await_count, 5)
        self.assertEqual(render_au_mock.await_args.kwargs["vinfo"]["title"], "Song")
        self.assertEqual(render_au_mock.await_args.kwargs["vcomments"], expected_comments)

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
            patch.object(views_audio.appredis, "mget", AsyncMock(return_value=[b"url", b"999"])),
            patch.object(views_audio, "render_template_with_theme", AsyncMock(return_value="<html>al</html>")),
        ):

            async def hit_al():
                async with app.test_request_context("/audio_list/am12345"):
                    return await views_audio.audio_list_view("am12345")

            await self._run_coalesced(
                [hit_al() for _ in range(5)], al_gate, sf=views_audio._audiolist_songs_sf, sf_key="12345"
            )

        self.assertEqual(al_gate.calls, 1)
        self.assertEqual(fake_track.get_download_url.await_count, 0)

        stale_song_cache = {
            "songs": [{"id": 100, "title": "Old", "duration": 10}],
            "ainfo": {"title": "OldSong", "cover": "", "intro": "", "author": "OldAuthor", "mid": 1, "statistic": {}},
            "list_info": {"title": "List"},
        }
        new_al = MagicMock()
        new_al.get_song_list = AsyncMock(return_value={"data": [{"id": 200, "title": "New", "duration": 20}]})
        new_al.get_info = AsyncMock(return_value={"title": "List"})
        new_track = MagicMock()
        new_track.get_info = AsyncMock(
            return_value={
                "title": "NewSong",
                "cover": "",
                "intro": "",
                "author": "NewAuthor",
                "mid": 2,
                "statistic": {},
            }
        )
        new_track.get_download_url = AsyncMock(return_value={"cdns": ["https://example.com/song200.mp3"]})
        render_al_mock = AsyncMock(return_value="<html>new</html>")
        pair_write = AsyncMock()
        with (
            patch.object(views_audio, "cache_minutes", return_value=10),
            patch.object(views_audio, "cache_get", AsyncMock(side_effect=[None, None, stale_song_cache])),
            patch.object(views_audio, "cache_set", AsyncMock()),
            patch.object(views_audio.audio, "AudioList", return_value=new_al),
            patch.object(views_audio.audio, "Audio", return_value=new_track),
            patch.object(
                views_audio.comment, "get_comments", AsyncMock(return_value={"page": {"count": 0}, "replies": []})
            ),
            patch.object(views_audio.appredis, "exists", AsyncMock(return_value=True)),
            patch.object(views_audio.appredis, "mget", AsyncMock(return_value=[b"old-url", b"100"])),
            patch.object(views_audio.appredis, "eval", pair_write),
            patch.object(views_audio, "render_template_with_theme", render_al_mock),
        ):
            async with app.test_request_context("/audio_list/am22222"):
                await views_audio.audio_list_view("am22222")
        self.assertEqual(render_al_mock.await_args.kwargs["vinfo"]["title"], "NewSong")
        self.assertEqual(new_track.get_download_url.await_count, 1)
        pair_write.assert_awaited_once_with(
            views_audio._AUDIO_PAIR_SET,
            2,
            "mikuinv_am22222_0_0",
            "mikuinv_am22222_0_0_song",
            "https://example.com/song200.mp3",
            "200",
        )
