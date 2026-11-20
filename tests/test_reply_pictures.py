# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest

from miku.core.url_signing import sign_pic_target
from miku.media.transformers import extract_reply_pictures
from miku.state import app
from miku.ui.renderer import render_template_with_theme

PIC_1 = "https://i0.hdslb.com/bfs/new_dyn/aaa111.jpg"
PIC_2 = "http://i1.hdslb.com/bfs/album/bbb222.png"
PIC_GIF = "https://i0.hdslb.com/bfs/new_dyn/ccc333.gif"


class ExtractReplyPicturesTests(unittest.TestCase):
    def test_single_picture_uses_single_thumbnail_suffix(self):
        pics = extract_reply_pictures(
            {
                "message": "look",
                "pictures": [
                    {
                        "img_src": PIC_1,
                        "img_width": 1080,
                        "img_height": 1440,
                        "img_size": 321.5,
                    }
                ],
            }
        )
        self.assertEqual(len(pics), 1)
        self.assertEqual(pics[0]["src"], sign_pic_target("i0.hdslb.com/bfs/new_dyn/aaa111.jpg"))
        self.assertEqual(pics[0]["thumb"], sign_pic_target("i0.hdslb.com/bfs/new_dyn/aaa111.jpg@480w.webp"))
        self.assertEqual(pics[0]["width"], 1080)
        self.assertEqual(pics[0]["height"], 1440)
        self.assertFalse(pics[0]["is_gif"])

    def test_multiple_pictures_use_grid_thumbnail_suffix(self):
        pics = extract_reply_pictures(
            {
                "pictures": [
                    {"img_src": PIC_1, "img_width": 800, "img_height": 600},
                    {"img_src": PIC_2, "img_width": 600, "img_height": 600},
                ]
            }
        )
        self.assertEqual(len(pics), 2)
        self.assertEqual(pics[0]["thumb"], sign_pic_target("i0.hdslb.com/bfs/new_dyn/aaa111.jpg@240w_240h_1c.webp"))
        self.assertEqual(pics[1]["thumb"], sign_pic_target("i1.hdslb.com/bfs/album/bbb222.png@240w_240h_1c.webp"))

    def test_gif_keeps_original_src_for_thumb(self):
        pics = extract_reply_pictures(
            {
                "pictures": [
                    {"img_src": PIC_GIF, "img_width": 400, "img_height": 400},
                    {"img_src": PIC_1, "play_gif_thumbnail": True},
                ]
            }
        )
        self.assertEqual(len(pics), 2)
        self.assertTrue(pics[0]["is_gif"])
        self.assertEqual(pics[0]["thumb"], pics[0]["src"])
        self.assertTrue(pics[1]["is_gif"])
        self.assertEqual(pics[1]["thumb"], pics[1]["src"])

    def test_preserves_existing_at_suffix_without_doubling(self):
        url = "https://i0.hdslb.com/bfs/new_dyn/aaa111.jpg@300w.webp"
        pics = extract_reply_pictures({"pictures": [{"img_src": url}]})
        self.assertEqual(len(pics), 1)
        expected = sign_pic_target("i0.hdslb.com/bfs/new_dyn/aaa111.jpg@300w.webp")
        self.assertEqual(pics[0]["src"], expected)
        self.assertEqual(pics[0]["thumb"], expected)

    def test_allows_protocol_relative_and_strips_query_and_fragment(self):
        pics = extract_reply_pictures({"pictures": [{"img_src": "//i0.hdslb.com/bfs/new_dyn/a.jpg?token=1#hash"}]})
        self.assertEqual(len(pics), 1)
        self.assertEqual(pics[0]["src"], sign_pic_target("i0.hdslb.com/bfs/new_dyn/a.jpg"))
        self.assertNotIn("token", pics[0]["src"])
        self.assertNotIn("hash", pics[0]["src"])

    def test_filters_out_unsafe_or_foreign_urls(self):
        pics = extract_reply_pictures(
            {
                "pictures": [
                    {"img_src": "https://evil.example/a.jpg"},
                    {"img_src": "javascript:alert(1)"},
                    {"img_src": "https://i0.hdslb.com/../secret.jpg"},
                    {"img_src": 'https://i0.hdslb.com/a".jpg'},
                    "not-a-dict",
                    {"img_src": PIC_1},
                ]
            }
        )
        self.assertEqual(len(pics), 1)
        self.assertEqual(pics[0]["src"], sign_pic_target("i0.hdslb.com/bfs/new_dyn/aaa111.jpg"))

    def test_caps_at_nine_pictures(self):
        raw = [{"img_src": f"https://i0.hdslb.com/bfs/new_dyn/p{i}.jpg"} for i in range(15)]
        pics = extract_reply_pictures({"pictures": raw})
        self.assertEqual(len(pics), 9)

    def test_degenerate_inputs_and_dimensions(self):
        for bad in (None, "", 123, [], {}, {"pictures": None}, {"pictures": []}, {"pictures": "bad"}):
            self.assertEqual(extract_reply_pictures(bad), [])

        pics = extract_reply_pictures(
            {
                "pictures": [
                    {"img_src": PIC_1, "img_width": True, "img_height": -10},
                    {"img_src": PIC_2, "img_width": "bad", "img_height": 0},
                ]
            }
        )
        self.assertIsNone(pics[0]["width"])
        self.assertIsNone(pics[0]["height"])
        self.assertIsNone(pics[1]["width"])
        self.assertIsNone(pics[1]["height"])


class CommentPicturesTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_comment_items_and_sub_comments_render_proxied_pictures(self):
        reply = {
            "rpid": 12345,
            "member": {
                "mid": 1,
                "uname": "tester",
                "avatar": "https://i0.hdslb.com/bfs/face/avatar.jpg",
                "level_info": {"current_level": 5},
            },
            "content": {
                "message": "check this",
                "pictures": [
                    {"img_src": PIC_1, "img_width": 640, "img_height": 480},
                    {"img_src": PIC_GIF, "img_width": 200, "img_height": 200},
                ],
            },
            "ctime": 1700000000,
            "like": 10,
            "rcount": 0,
        }
        async with app.test_request_context("/"):
            html_main = await render_template_with_theme(
                "components/comment_items.html",
                vcomments={"replies": [reply], "next_offset": "", "is_end": True, "mode": 3},
                vid="BV1xx411c7mD",
            )
            html_sub = await render_template_with_theme(
                "components/sub_comments.html",
                sub_comments={"replies": [reply], "page": {"num": 1, "size": 20, "count": 1}},
                parent_rpid=12345,
            )

        expected_full = sign_pic_target("i0.hdslb.com/bfs/new_dyn/aaa111.jpg")
        expected_thumb = sign_pic_target("i0.hdslb.com/bfs/new_dyn/aaa111.jpg@240w_240h_1c.webp")
        expected_gif = sign_pic_target("i0.hdslb.com/bfs/new_dyn/ccc333.gif")

        for rendered in (html_main, html_sub):
            self.assertIn("comment-pictures", rendered)
            self.assertIn(f'data-full-src="{expected_full}"', rendered)
            self.assertIn(f'src="{expected_thumb}"', rendered)
            self.assertIn(f'src="{expected_gif}"', rendered)
            self.assertIn(">GIF<", rendered)
            self.assertNotIn("https://i0.hdslb.com", rendered)


if __name__ == "__main__":
    unittest.main()
