import asyncio
import json
import unittest
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import miku.media.extra as extra


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
