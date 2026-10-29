import unittest

from transformers import render_reply_content, strip_search_highlight

try:
    import miku_core_rs
except ModuleNotFoundError as exc:
    if exc.name != "miku_core_rs":
        raise
    miku_core_rs = None


class TestTextRs(unittest.TestCase):
    def test_strip_search_highlight(self):
        cases = [
            ('<em class="keyword">原神</em>', "原神"),
            ("Hello <em class='keyword'>World</em>!", "Hello World!"),
            ("A &amp; B &lt; C &gt; D", "A & B < C > D"),
            ("<em class=\"keyword\">Rock &amp; Roll</em>", "Rock & Roll"),
            ("", ""),
            (None, ""),
            ("No highlights here", "No highlights here"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(strip_search_highlight(inp), expected)

    def test_render_reply_content_with_emotes(self):
        doge_url = "https://i0.hdslb.com/bfs/emote/doge.png"
        content_dict = {
            "message": "Hello [doge] and <script>bad()</script>",
            "emote": {
                "[doge]": {
                    "url": doge_url,
                    "meta": {"size": 1},
                }
            },
        }
        rendered = render_reply_content(content_dict)
        self.assertIn('/proxy/pic/i0.hdslb.com/bfs/emote/doge.png', rendered)
        self.assertIn('alt="[doge]"', rendered)
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', rendered)
        self.assertNotIn('<script>', rendered)

    @unittest.skipIf(miku_core_rs is None, "miku_core_rs is not installed")
    def test_rust_text_direct_parity(self):
        self.assertTrue(hasattr(miku_core_rs, "strip_search_highlight"))
        self.assertTrue(hasattr(miku_core_rs, "render_reply_content"))

        # Check strip_search_highlight parity
        test_str = '<em class="keyword">Miku &amp; Luka</em>'
        self.assertEqual(
            miku_core_rs.strip_search_highlight(test_str),
            strip_search_highlight(test_str),
        )

        # Check render_reply_content parity
        doge_url = "https://i0.hdslb.com/bfs/emote/doge.png"
        msg = "Hi [doge] & [unknown] <test>"
        emote_dict = {
            "[doge]": {
                "url": doge_url,
                "meta": {"size": 1},
            }
        }
        rs_out = miku_core_rs.render_reply_content(msg, emote_dict)
        py_out = render_reply_content({"message": msg, "emote": emote_dict})
        self.assertEqual(rs_out, py_out)


if __name__ == "__main__":
    unittest.main()
