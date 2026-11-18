# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest
from unittest.mock import AsyncMock, patch

import miku.ui.filters  # noqa: F401
from miku.core.url_signing import (
    build_signed_pic_url,
    resign_pic_url,
    resign_pic_urls_in_html,
    sign_danmaku_url,
    sign_dash_manifest_url,
    sign_dash_track_url,
    sign_pic_target,
    sign_subtitle_list_url,
    sign_subtitle_vtt_url,
    verify_danmaku_sig,
    verify_dash_manifest_sig,
    verify_dash_track_sig,
    verify_signed_pic_subpath,
    verify_subtitle_list_sig,
    verify_subtitle_vtt_sig,
)
from miku.media.extra import _proxied_pic_url
from miku.state import app


class TestUrlSigning(unittest.TestCase):
    def test_pic_sign_and_verify_roundtrip(self):
        target = "i0.hdslb.com/bfs/archive/cover.jpg@320w.webp"
        signed = sign_pic_target(target)
        self.assertTrue(signed.startswith("/proxy/pic/s/"))
        self.assertNotIn("?", signed)
        self.assertEqual(verify_signed_pic_subpath(signed[len("/proxy/pic/") :]), target)

        qtarget = "i0.hdslb.com/bfs/pic.jpg?foo=1&bar=2"
        qsigned = sign_pic_target(qtarget)
        path_only, qs = qsigned.split("?", 1)
        subpath = path_only[len("/proxy/pic/") :]
        self.assertEqual(verify_signed_pic_subpath(subpath, qs), "i0.hdslb.com/bfs/pic.jpg")
        self.assertIsNone(verify_signed_pic_subpath(subpath, "foo=2"))
        self.assertIsNone(verify_signed_pic_subpath("i0.hdslb.com/bfs/a.jpg"))

    def test_build_signed_pic_url_normalization_and_secret_rotation(self):
        self.assertEqual(build_signed_pic_url(""), "")
        signed_https = build_signed_pic_url("https://i0.hdslb.com/bfs/a.jpg")
        self.assertEqual(signed_https, build_signed_pic_url("http://i0.hdslb.com/bfs/a.jpg"))
        self.assertEqual(signed_https, build_signed_pic_url("//i0.hdslb.com/bfs/a.jpg"))
        self.assertEqual(signed_https, build_signed_pic_url("i0.hdslb.com/bfs/a.jpg"))
        self.assertEqual(signed_https, build_signed_pic_url("https://i0.hdslb.com/bfs/a.jpg?#frag"))

        orig_key = app.secret_key
        try:
            app.secret_key = "rotated-secret-key-for-test"
            signed_rotated = build_signed_pic_url("https://i0.hdslb.com/bfs/a.jpg")
            self.assertNotEqual(signed_https, signed_rotated)
            self.assertIsNone(verify_signed_pic_subpath(signed_https[len("/proxy/pic/") :]))
            self.assertEqual(
                verify_signed_pic_subpath(signed_rotated[len("/proxy/pic/") :]),
                "i0.hdslb.com/bfs/a.jpg",
            )
        finally:
            app.secret_key = orig_key

    def test_resign_pic_helpers_and_filters(self):
        filter_fn = app.jinja_env.filters["pic"]
        u1 = filter_fn("https://i0.hdslb.com/bfs/face/1.jpg")
        self.assertEqual(verify_signed_pic_subpath(u1[len("/proxy/pic/") :]), "i0.hdslb.com/bfs/face/1.jpg")

        u2 = _proxied_pic_url("https://i0.hdslb.com/bfs/article/2.png?w=600")
        path_only, qs = u2.split("?", 1)
        self.assertEqual(
            verify_signed_pic_subpath(path_only[len("/proxy/pic/") :], qs), "i0.hdslb.com/bfs/article/2.png"
        )

        orig_key = app.secret_key
        try:
            app.secret_key = "old-key"
            stale_img = sign_pic_target("i0.hdslb.com/bfs/article/stale.jpg?w=100")
            stale_cover = sign_pic_target("i0.hdslb.com/bfs/article/cover.jpg")
            html = (
                "<p>hello</p>"
                '<img src="/proxy/pic/i0.hdslb.com/bfs/article/legacy.png" alt="legacy">'
                f'<img src="{stale_img}" alt="stale">'
            )
            app.secret_key = "new-key"
            resigned = resign_pic_urls_in_html(html)
            self.assertIn(f'src="{sign_pic_target("i0.hdslb.com/bfs/article/legacy.png")}"', resigned)
            self.assertIn(f'src="{sign_pic_target("i0.hdslb.com/bfs/article/stale.jpg?w=100")}"', resigned)
            self.assertEqual(resign_pic_url(stale_cover), sign_pic_target("i0.hdslb.com/bfs/article/cover.jpg"))
        finally:
            app.secret_key = orig_key

    def test_danmaku_subtitle_and_dash_signing_and_cross_context_isolation(self):
        dm_url = sign_danmaku_url("BV1xx411c7mD", 2)
        dm_sig = dm_url.split("/")[4]
        self.assertTrue(dm_url.startswith("/res/danmaku/s/"))
        self.assertTrue(dm_url.endswith("/BV1xx411c7mD:2"))
        self.assertTrue(verify_danmaku_sig(dm_sig, "BV1xx411c7mD", 2))
        self.assertFalse(verify_danmaku_sig(dm_sig, "BV1xx411c7mD", 0))
        self.assertFalse(verify_danmaku_sig(None, "BV1xx411c7mD", 2))
        self.assertFalse(verify_subtitle_list_sig(dm_sig, "BV1xx411c7mD", 2))

        sub_list_url = sign_subtitle_list_url("BV1xx411c7mD", 1)
        sub_list_sig = sub_list_url.split("/")[4]
        self.assertTrue(verify_subtitle_list_sig(sub_list_sig, "BV1xx411c7mD", 1))
        self.assertFalse(verify_danmaku_sig(sub_list_sig, "BV1xx411c7mD", 1))

        sub_vtt_url = sign_subtitle_vtt_url("BV1xx411c7mD", 0, "zh-CN")
        sub_vtt_sig = sub_vtt_url.split("/")[4]
        self.assertTrue(sub_vtt_url.endswith("/BV1xx411c7mD:0:zh-CN"))
        self.assertTrue(verify_subtitle_vtt_sig(sub_vtt_sig, "BV1xx411c7mD", 0, "zh-CN"))
        self.assertFalse(verify_subtitle_vtt_sig(sub_vtt_sig, "BV1xx411c7mD", 0, "en-US"))

        mpd_url = sign_dash_manifest_url("BV1xx411c7mD", 0)
        mpd_sig = mpd_url.split("/")[4]
        self.assertTrue(mpd_url.startswith("/video/dash/s/"))
        self.assertTrue(mpd_url.endswith("/BV1xx411c7mD/0/manifest.mpd"))
        self.assertTrue(verify_dash_manifest_sig(mpd_sig, "BV1xx411c7mD", 0))
        self.assertFalse(verify_dash_manifest_sig(mpd_sig, "BV1xx411c7mD", 1))

        dash_url = sign_dash_track_url("BV1xx411c7mD", 0, "video", 80, 7)
        dash_sig = dash_url.split("/")[4]
        self.assertTrue(dash_url.startswith("/proxy/dash/s/"))
        self.assertTrue(dash_url.endswith("/BV1xx411c7mD/0/video/80/7"))
        self.assertTrue(verify_dash_track_sig(dash_sig, "BV1xx411c7mD", 0, "video", 80, 7))
        self.assertFalse(verify_dash_track_sig(dash_sig, "BV1xx411c7mD", 0, "audio", 80, 7))
        self.assertFalse(verify_dash_track_sig(dash_sig, "BV1xx411c7mD", 0, "video", 64, 7))

    def test_danmaku_and_subtitle_jinja_filters(self):
        dm_filter = app.jinja_env.filters["danmaku_url"]
        sub_filter = app.jinja_env.filters["subtitle_url"]
        self.assertEqual(dm_filter("BV1xx411c7mD:3"), sign_danmaku_url("BV1xx411c7mD", 3))
        self.assertEqual(dm_filter("BV1xx411c7mD"), sign_danmaku_url("BV1xx411c7mD", 0))
        self.assertEqual(sub_filter("BV1xx411c7mD:0:zh-CN"), sign_subtitle_vtt_url("BV1xx411c7mD", 0, "zh-CN"))

    def test_pic_signing_edge_cases(self):
        non_ascii_sig = "é" * 22
        self.assertFalse(verify_danmaku_sig(non_ascii_sig, "BV1xx411c7mD", 0))
        self.assertIsNone(verify_signed_pic_subpath(f"s/{non_ascii_sig}/i0.hdslb.com/bfs/a.jpg"))

        ref_signed = build_signed_pic_url("i0.hdslb.com/bfs/a.jpg?ref=https://www.bilibili.com")
        ref_path, ref_qs = ref_signed.split("?", 1)
        self.assertEqual(verify_signed_pic_subpath(ref_path[len("/proxy/pic/") :], ref_qs), "i0.hdslb.com/bfs/a.jpg")
        self.assertEqual(ref_qs, "ref=https://www.bilibili.com")

        quote_signed = sign_pic_target("i0.hdslb.com/bfs/a.jpg?caption=it's&ok=1")
        quote_path, quote_qs = quote_signed.split("?", 1)
        self.assertEqual(quote_qs, "caption=it%27s&ok=1")
        self.assertEqual(
            verify_signed_pic_subpath(quote_path[len("/proxy/pic/") :], "caption=it%27s&ok=1"),
            "i0.hdslb.com/bfs/a.jpg",
        )

        slash_signed = sign_pic_target("i0.hdslb.com/bfs/a/b.jpg")
        slash_sig = slash_signed.split("/")[4]
        self.assertIsNone(verify_signed_pic_subpath(f"s/{slash_sig}/i0.hdslb.com/bfs/a%2Fb.jpg"))


class TestSpaceArticleCacheResign(unittest.IsolatedAsyncioTestCase):
    async def test_read_view_resigns_cached_article_images_after_key_rotation(self):
        from miku.views.space import read_view

        orig_key = app.secret_key
        try:
            app.secret_key = "key-before-restart"
            stale_src = sign_pic_target("i0.hdslb.com/bfs/article/cached.jpg")
            stale_cover = sign_pic_target("i0.hdslb.com/bfs/article/cover.jpg")
            cached_payload = {
                "arinfo": {"title": "Test", "author": {"mid": 1, "name": "up", "face": ""}, "covers": [stale_cover]},
                "content": f'<p>text</p><img src="{stale_src}">',
            }
            app.secret_key = "key-after-restart"
            expected_src = sign_pic_target("i0.hdslb.com/bfs/article/cached.jpg")
            expected_cover = sign_pic_target("i0.hdslb.com/bfs/article/cover.jpg")

            rendered_arinfo = {}

            async def fake_render(_tpl, **kw):
                rendered_arinfo.update(kw["arinfo"])
                return kw["article_content"]

            with (
                patch("miku.views.space.cache_minutes", return_value=30),
                patch("miku.views.space.cache_get", new=AsyncMock(return_value=cached_payload)),
                patch("miku.views.space._get_anonymous_cookies", new=AsyncMock(return_value={})),
                patch("miku.views.space.render_template_with_theme", new=AsyncMock(side_effect=fake_render)),
                patch("miku.views.space._fetch_article_comments", new=AsyncMock(return_value=({}, "123", 12, False))),
            ):
                async with app.test_request_context("/read/cv123"):
                    resp = await read_view("cv123")
            body = await resp.get_data(as_text=True)
            self.assertIn(expected_src, body)
            self.assertNotIn(stale_src, body)
            self.assertEqual(rendered_arinfo["covers"], [expected_cover])
        finally:
            app.secret_key = orig_key


if __name__ == "__main__":
    unittest.main()
