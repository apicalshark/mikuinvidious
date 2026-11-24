# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import miku.views.video as views_video
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
VIDEO_HTML = ROOT / "templates" / "themes" / "modern" / "video.html"


class TestM3Video(unittest.TestCase):
    def setUp(self):
        self.env = Environment(loader=FileSystemLoader("templates"))
        self.env.globals["_"] = lambda s: s
        self.env.filters["pic"] = lambda s: s
        self.env.filters["date"] = lambda s: s
        self.env.filters["intsep"] = lambda s: str(s)
        self.env.filters["format_desc"] = lambda s: s
        self.env.filters["reply_content"] = lambda s: s
        self.env.filters["reply_pictures"] = lambda s: []
        self.env.filters["ugc_season"] = lambda s: None
        self.env.filters["secdur"] = lambda s: str(s)
        self.env.filters["danmaku_url"] = lambda s: f"/res/danmaku/s/test/{s}"
        self.env.filters["subtitle_url"] = lambda s: f"/res/subtitle/s/test/{s}"

    def test_video_template_syntax_and_structure(self):
        html = VIDEO_HTML.read_text(encoding="utf-8")
        # Invariant: YouTube 2-column layout preserved
        self.assertIn("lg:col-span-3", html)
        self.assertIn("lg:col-span-1", html)
        # Template compiles cleanly
        template = self.env.get_template("themes/modern/video.html")
        self.assertIsNotNone(template)

    def test_video_template_uses_m3_surface_container(self):
        html = VIDEO_HTML.read_text(encoding="utf-8")
        self.assertIn("bg-surface-container-low", html)

    def test_video_template_renders_with_mock_context(self):
        template = self.env.get_template("themes/modern/video.html")
        context = {
            "vinfo": {
                "bvid": "BV1test",
                "title": "Test Video Title",
                "pic": "http://example.com/pic.jpg",
                "desc": "Test Description",
                "pubdate": 1600000000,
                "tid": 1,
                "tname": "Animation",
                "stat": {
                    "view": 10000,
                    "like": 500,
                    "coin": 200,
                    "favorite": 100,
                    "share": 50,
                    "danmaku": 30,
                },
                "owner": {
                    "mid": 123456,
                    "name": "Test Creator",
                    "face": "http://example.com/face.jpg",
                },
            },
            "vset": [{"page": 1, "part": "P1", "cid": 123}],
            "idx": 0,
            "bvid": "BV1test",
            "is_live": False,
            "vtags": [{"tag_name": "Tag1"}],
            "related": [],
            "audio_only": False,
            "dash": None,
            "user": None,
            "asset_version": "1",
            "active_tab": "video",
            "i18n_catalog": {},
            "csp_nonce": "test-nonce",
            "locale": "en",
            "vcomments": {"page": {"count": 10}, "replies": []},
            "opencc_enabled": False,
            "vfavored": False,
        }
        rendered = template.render(**context)
        self.assertIn("Test Video Title", rendered)
        self.assertIn("Test Creator", rendered)
        self.assertIn('title="Likes"', rendered)

        player_tpl = self.env.get_template("themes/modern/components/player_part.html")
        rendered_signed = player_tpl.render(
            vid="BV1test",
            vinfo=context["vinfo"],
            idx=2,
            supported_src=[],
            is_live=False,
            is_dash=True,
            dash_url="/video/dash/s/test/BV1test/2/manifest.mpd",
            subtitles=[{"lan": "zh-CN", "srclang": "zh-CN", "label": "Chinese", "auto_generated": False}],
            paywall=False,
        )
        self.assertIn('data-danmaku-url="/res/danmaku/s/test/BV1test:2"', rendered_signed)
        self.assertIn('src="/res/subtitle/s/test/BV1test:2:zh-CN"', rendered_signed)

    def test_video_template_renders_audio_mode_and_progressive_audio(self):
        template = self.env.get_template("themes/modern/video.html")
        base_ctx = {
            "vid": "BV1test",
            "vinfo": {
                "bvid": "BV1test",
                "title": "Audio Test",
                "pic": "http://example.com/pic.jpg",
                "desc": "Desc",
                "pubdate": 1600000000,
                "tid": 1,
                "tname": "Music",
                "stat": {"view": 100, "like": 10, "coin": 5, "favorite": 3, "share": 1},
                "owner": {"mid": 123, "name": "Creator", "face": "http://example.com/f.jpg"},
            },
            "vset": [{"page": 1, "part": "P1"}],
            "idx": 0,
            "is_live": False,
            "is_audio": True,
            "is_progressive_audio": False,
            "vtags": [],
            "vrelated": [
                {
                    "bvid": "BV1rel",
                    "title": "Rel",
                    "pic": "http://example.com/r.jpg",
                    "duration": 10,
                    "owner": {"name": "O"},
                    "stat": {"view": 1},
                }
            ],
            "supported_src": [],
            "ato": False,
            "site_allow_download": True,
            "csrf_token": "tok",
            "asset_version": "1",
            "i18n_catalog": {},
            "locale": "en",
            "csp_nonce": "nonce",
            "vcomments": {"page": {"count": 0}, "replies": []},
        }
        rendered_audio = template.render(**base_ctx)
        self.assertIn("window.is_audio = true;", rendered_audio)
        self.assertIn("dash.min.js", rendered_audio)
        self.assertNotIn("danmaku.min.js", rendered_audio)
        self.assertIn('href="/video/BV1test?listen=0"', rendered_audio)
        self.assertIn('href="/video/BV1rel?listen=1"', rendered_audio)

        prog_ctx = dict(base_ctx)
        prog_ctx.update(
            {
                "vid": "au999",
                "is_progressive_audio": True,
                "comment_type": 14,
                "direct_download_url": "/proxy/video/au999_0_0?dl=1",
            }
        )
        prog_ctx["vinfo"] = dict(base_ctx["vinfo"], bvid="au999")
        rendered_prog = template.render(**prog_ctx)
        self.assertIn("window.is_progressive_audio = true;", rendered_prog)
        self.assertIn('data-direct-url="/proxy/video/au999_0_0?dl=1"', rendered_prog)
        self.assertIn('data-comment-type="14"', rendered_prog)
        self.assertNotIn("dash.min.js", rendered_prog)
        self.assertNotIn('id="avswitch"', rendered_prog)

        player_tpl = self.env.get_template("themes/modern/components/player_part.html")
        rendered_player = player_tpl.render(
            vid="BV1test",
            vinfo=base_ctx["vinfo"],
            idx=0,
            supported_src=[],
            is_live=False,
            is_dash=True,
            dash_url="/video/dash/s/test/BV1test/0/manifest.mpd?audio_only=1",
            subtitles=[],
            paywall=False,
            is_audio=True,
            download_options={
                "videos": [],
                "audios": [{"id": 30280, "codecid": 0, "bandwidth": 192000}],
                "progressive": [],
            },
        )
        self.assertIn("bg-transparent!", rendered_player)
        self.assertIn("window.download_options =", rendered_player)
        self.assertNotIn("data-danmaku-url", rendered_player)
        self.assertNotIn("danmaku-toggle", rendered_player)
        self.assertNotIn("data-fullscreen-btn", rendered_player)


class TestAudioAndListenViews(unittest.IsolatedAsyncioTestCase):
    async def test_video_listen_and_audio_views_render_unified_video_template(self):
        import miku.views.audio as views_audio
        from miku.app import app

        inst = MagicMock()
        inst.get_info = AsyncMock(
            return_value={"bvid": "BV1xx411c7mD", "title": "Listen", "owner": {"mid": 1, "name": "UP", "face": ""}}
        )
        inst.get_tags = AsyncMock(return_value=[])
        inst.get_related = AsyncMock(return_value=[])
        inst.get_pages = AsyncMock(return_value=[{"page": 1, "part": "P1"}])

        render_mock = AsyncMock(return_value="<html>ok</html>")
        with (
            patch.object(views_video.video, "Video", return_value=inst),
            patch.object(views_video, "cache_minutes", return_value=0),
            patch.object(views_video, "_fetch_owner_meta", AsyncMock(return_value={})),
            patch.object(views_video, "render_template_with_theme", render_mock),
        ):
            client = app.test_client()
            resp1 = await client.get("/video_listen/BV1xx411c7mD")
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(render_mock.await_args.args[0], "video.html")
            self.assertTrue(render_mock.await_args.kwargs["is_audio"])
            self.assertFalse(render_mock.await_args.kwargs["is_progressive_audio"])

            resp2 = await client.get("/video/BV1xx411c7mD?listen=1")
            self.assertEqual(resp2.status_code, 200)
            self.assertTrue(render_mock.await_args.kwargs["is_audio"])

        track = MagicMock()
        track.get_info = AsyncMock(return_value={"title": "Song", "author": "Singer", "mid": 7})
        track.get_download_url = AsyncMock(return_value={"cdns": ["https://cdn.example/a.m4a"]})
        render_audio_mock = AsyncMock(return_value="<html>audio</html>")
        with (
            patch.object(views_audio, "cache_minutes", return_value=0),
            patch.object(views_audio.audio, "Audio", return_value=track),
            patch.object(
                views_audio, "_fetch_audio_comments_safe", AsyncMock(return_value={"page": {"count": 0}, "replies": []})
            ),
            patch.object(views_audio.appredis, "exists", AsyncMock(return_value=1)),
            patch.object(views_audio, "render_template_with_theme", render_audio_mock),
        ):
            client = app.test_client()
            resp_au = await client.get("/audio/au12345")
            self.assertEqual(resp_au.status_code, 200)
            self.assertEqual(render_audio_mock.await_args.args[0], "video.html")
            self.assertTrue(render_audio_mock.await_args.kwargs["is_audio"])
            self.assertTrue(render_audio_mock.await_args.kwargs["is_progressive_audio"])
            self.assertEqual(render_audio_mock.await_args.kwargs["comment_type"], 14)
            self.assertEqual(
                render_audio_mock.await_args.kwargs["direct_download_url"], "/proxy/video/au12345_0_0?dl=1"
            )


class TestOwnerMeta(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_owner_meta_normalizes_missing_level_info_to_dict(self):
        fake_user = MagicMock()
        fake_user.get_user_info = AsyncMock(return_value={"name": "Test", "level": None, "level_info": None})
        with (
            patch.object(views_video, "cache_get", AsyncMock(return_value=None)),
            patch.object(views_video, "cache_set", AsyncMock()),
            patch.object(views_video.user, "User", return_value=fake_user),
        ):
            meta = await views_video._fetch_owner_meta({"owner": {"mid": 12345}})

        self.assertEqual(meta["level_info"], {})
        self.assertIsNone(meta["level_info"].get("current_level"))
