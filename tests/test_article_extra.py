# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import json
import unittest
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import miku.media.extra as extra
from miku.core.url_signing import sign_pic_target


class TestArticleExtra(unittest.IsolatedAsyncioTestCase):
    def test_opus_initial_state_escapes_html_and_sanitizes_lang_and_color(self):
        state = {
            "detail": {
                "modules": [
                    {
                        "module_type": "MODULE_TYPE_CONTENT",
                        "module_content": {
                            "paragraphs": [
                                {
                                    "para_type": 1,
                                    "text": {
                                        "nodes": [
                                            {
                                                "rich": {
                                                    "text": '<img src=x onerror="alert(1)">',
                                                    "jump_url": 'https://example.com/?a=1" onclick="alert(1)',
                                                    "emoji": {"icon_url": "https://i0.hdslb.com/bfs/emote/test.png"},
                                                }
                                            },
                                            {
                                                "word": {
                                                    "words": "<script>bad()</script>",
                                                    "style": {"color": "red; background:url(https://evil)"},
                                                }
                                            },
                                            {
                                                "word": {
                                                    "words": "safe-color",
                                                    "style": {"color": "#112233"},
                                                }
                                            },
                                        ]
                                    },
                                },
                                {
                                    "para_type": 7,
                                    "code": {
                                        "lang": 'python" onclick="alert(1)',
                                        "content": "<script>alert(1)</script>",
                                    },
                                },
                            ]
                        },
                    }
                ]
            }
        }
        raw_html = f"<script>window.__INITIAL_STATE__ = {json.dumps(state)};</script>"
        rendered = extra.article_to_html(raw_html)

        self.assertNotIn("<script", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertNotIn('onclick="alert', rendered)
        self.assertNotIn("background:url", rendered)
        self.assertIn('alt="&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"', rendered)
        self.assertIn("language-text", rendered)
        self.assertIn("#112233", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)

    def test_opus_colors_empty_paragraphs_dividers_and_metadata(self):
        state = {
            "detail": {
                "modules": [
                    {
                        "module_type": "MODULE_TYPE_TOP",
                        "module_top": {
                            "display": {
                                "album": {
                                    "pics": [
                                        {
                                            "url": "https://i0.hdslb.com/bfs/article/cover.png",
                                            "width": 640,
                                            "height": 188,
                                        }
                                    ]
                                }
                            }
                        },
                    },
                    {
                        "module_type": "MODULE_TYPE_TITLE",
                        "module_title": {"text": "Test Article"},
                    },
                    {
                        "module_type": "MODULE_TYPE_CONTENT",
                        "module_content": {
                            "paragraphs": [
                                {
                                    "para_type": 1,
                                    "align": 1,
                                    "text": {
                                        "nodes": [
                                            {
                                                "type": "TEXT_NODE_TYPE_WORD",
                                                "word": {
                                                    "words": "Colorful Bold",
                                                    "color": "var(--Cy4, #4FD3D1)",
                                                    "style": {"bold": True},
                                                },
                                            }
                                        ]
                                    },
                                },
                                {
                                    "para_type": 1,
                                    "text": {
                                        "nodes": [
                                            {
                                                "type": "TEXT_NODE_TYPE_WORD",
                                                "word": {"words": "\n", "color": ""},
                                            }
                                        ]
                                    },
                                },
                                {
                                    "para_type": 2,
                                    "pic": {
                                        "pics": [
                                            {
                                                "url": "http://i0.hdslb.com/bfs/article/poster.jpg",
                                                "width": 500,
                                                "height": 707,
                                            }
                                        ]
                                    },
                                },
                                {
                                    "para_type": 2,
                                    "pic": {
                                        "pics": [
                                            {
                                                "url": "http://i0.hdslb.com/bfs/article/divider.png",
                                                "width": 124,
                                                "height": 32,
                                            }
                                        ]
                                    },
                                },
                            ]
                        },
                    },
                    {
                        "module_type": "MODULE_TYPE_EXTEND",
                        "module_extend": {
                            "items": [
                                {"text": "Miku"},
                                {"text": "LIVE"},
                                {"text": "Miku"},
                            ]
                        },
                    },
                    {
                        "module_type": "MODULE_TYPE_COPYRIGHT",
                        "module_copyright": {
                            "left_text": "Original content",
                            "right_text": "cv8036178",
                        },
                    },
                ]
            }
        }
        raw_html = f"<script>window.__INITIAL_STATE__ = {json.dumps(state)};</script>"
        rendered = extra.article_to_html(raw_html)
        info = extra.get_article_info(raw_html, "cv8036178")

        self.assertIn('style="color:#4FD3D1"', rendered.replace(" ", ""))
        self.assertIn('<strong><span style="color:#4FD3D1">Colorful Bold</span></strong>', rendered)
        self.assertIn('style="text-align:center"', rendered.replace(" ", ""))
        self.assertIn("<p><br>", rendered)
        self.assertIn(f'src="{sign_pic_target("i0.hdslb.com/bfs/article/poster.jpg")}"', rendered)
        self.assertIn('width="500"', rendered)
        self.assertIn('class="mx-auto rounded-xl"', rendered)
        self.assertIn(f'src="{sign_pic_target("i0.hdslb.com/bfs/article/divider.png")}"', rendered)
        self.assertIn('width="124"', rendered)
        self.assertIn('class="mx-auto"', rendered)

        self.assertEqual(info["covers"], [sign_pic_target("i0.hdslb.com/bfs/article/cover.png")])
        self.assertEqual(info["tags"], ["Miku", "LIVE"])
        self.assertEqual(
            info["copyright"],
            {"left_text": "Original content", "right_text": "cv8036178"},
        )

    def test_legacy_article_holder_preserves_empty_paragraph_and_var_color(self):
        raw_html = (
            '<div id="read-article-holder">'
            '<p><span style="background-color: #fff; color: var(--Ma3, #F7AEEB);">Hi</span></p>'
            "<p>\n</p>"
            '<img data-src="//i0.hdslb.com/bfs/article/div.png" width="124" height="32">'
            "</div>"
        )
        rendered = extra.article_to_html(raw_html)
        self.assertIn("color:#F7AEEB", rendered.replace(" ", ""))
        self.assertIn("<p><br>", rendered)
        self.assertIn('class="mx-auto"', rendered)
        self.assertNotIn("rounded-xl", rendered)

    def test_merge_article_viewinfo_cover_fallback(self):
        arinfo = {"title": "", "stats": {"view": 0}, "covers": []}
        extra.merge_article_viewinfo(
            arinfo,
            {
                "title": "Legacy Title",
                "stats": {"view": 99},
                "banner_url": "https://i0.hdslb.com/bfs/article/banner.jpg",
            },
        )
        self.assertEqual(arinfo["title"], "Legacy Title")
        self.assertEqual(arinfo["stats"]["view"], 99)
        self.assertEqual(arinfo["covers"], [sign_pic_target("i0.hdslb.com/bfs/article/banner.jpg")])

    async def test_article_to_any_pandoc_timeout_terminates_and_returns_empty(self):
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.terminate = MagicMock()
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=-15)

        async def slow_communicate(input=None):
            await asyncio.sleep(5)
            return b"", b""

        fake_proc.communicate = slow_communicate

        with (
            patch.object(extra, "_PANDOC_TIMEOUT", 0.02),
            patch.object(extra.asyncio, "create_subprocess_exec", AsyncMock(return_value=fake_proc)),
        ):
            out = await extra.article_to_any('<div id="read-article-holder"><p>hi</p></div>', "markdown")

        self.assertEqual(out, "")
        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_awaited()

    async def test_video_get_src_for_qn_pgc_fallback_strips_bili_ticket_and_no_redirects(self):
        fake_vi = MagicMock()
        fake_vi.credential = MagicMock()
        fake_vi.credential.sessdata = "sess_123"
        fake_vi.credential.get_cookies.return_value = {
            "SESSDATA": "sess_123",
            "bili_jct": "jct_123",
            "bili_ticket": "should_never_be_cookie",
            "bili_ticket_expires": "123456",
        }
        fake_vi.get_cid = AsyncMock(return_value=555)
        fake_vi.get_aid.return_value = 170001
        fake_vi.get_info = AsyncMock(return_value={"redirect_url": "https://www.bilibili.com/bangumi/play/ep42"})

        err_404 = RuntimeError("not found")
        err_404.code = -404

        seen_kwargs = {}

        async def fake_get(url, **kwargs):
            seen_kwargs.update(kwargs)
            resp = MagicMock()
            resp.json.return_value = {"code": 0, "result": {"durl": [{"url": "https://cdn.example/x.mp4"}]}}
            return resp

        fake_client = MagicMock()
        fake_client.get = AsyncMock(side_effect=fake_get)

        mock_api = MagicMock()
        mock_api.request = AsyncMock(side_effect=err_404)

        with (
            warnings.catch_warnings(),
            patch("miku.services.credential.is_cred_active", return_value=True),
            patch.object(extra, "Api", return_value=mock_api),
            patch.object(extra.Network, "get_async_client", AsyncMock(return_value=fake_client)),
        ):
            warnings.simplefilter("ignore", DeprecationWarning)
            res = await extra.video_get_src_for_qn(fake_vi, 0, quality=80)

        self.assertIn("durl", res)
        self.assertFalse(seen_kwargs.get("follow_redirects"))
        cookie_hdr = (seen_kwargs.get("headers") or {}).get("Cookie", "")
        self.assertIn("SESSDATA=sess_123", cookie_hdr)
        self.assertNotIn("bili_ticket", cookie_hdr)


if __name__ == "__main__":
    unittest.main()
