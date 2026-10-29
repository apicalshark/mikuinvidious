import unittest

from transformers import _proxied_emote_src, _render_reply_content_py, render_reply_content, strip_search_highlight


class TestText(unittest.TestCase):
    def test_strip_search_highlight(self):
        cases = [
            ('<em class="keyword">原神</em>', "原神"),
            ("Hello <em class='keyword'>World</em>!", "Hello World!"),
            ("A &amp; B &lt; C &gt; D", "A & B < C > D"),
            ('<em class="keyword">Rock &amp; Roll</em>', "Rock & Roll"),
            ("", ""),
            (None, ""),
            ("No highlights here", "No highlights here"),
            ('<EM class="keyword">Miku &amp; Luka</EM>', '<EM class="keyword">Miku & Luka</EM>'),
            ('<Em class="keyword">Miku</eM>', '<Em class="keyword">Miku</eM>'),
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
        self.assertIn("/proxy/pic/i0.hdslb.com/bfs/emote/doge.png", rendered)
        self.assertIn('alt="[doge]"', rendered)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)

    def test_rejects_literal_parent_path_segments(self):
        urls = [
            "https://i0.hdslb.com/../doge.png",
            "https://i0.hdslb.com/bfs/../doge.png",
            "//i0.hdslb.com/bfs/..",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(_proxied_emote_src(url))
                emote = {"[doge]": {"url": url}}
                self.assertEqual(_render_reply_content_py("[doge]", emote), "[doge]")

    def test_raw_path_check_ignores_query_and_fragment(self):
        for suffix in ("?next=/../doge.png", "#../doge.png", "?next=/..", "#/.."):
            with self.subTest(suffix=suffix):
                emote = {"[doge]": {"url": "https://i0.hdslb.com/bfs/emote/doge%20a.png" + suffix}}
                expected = _render_reply_content_py("[doge]", emote)
                self.assertIn("/proxy/pic/i0.hdslb.com/bfs/emote/doge%20a.png", expected)

    def test_falls_back_from_falsy_emote_urls(self):
        for value in (None, "", False, 0, [], {}):
            for fallback_key in ("gif_url", "url"):
                with self.subTest(value=value, fallback_key=fallback_key):
                    entry = {"webp_url": value, "gif_url": value, fallback_key: "https://i0.hdslb.com/doge.png"}
                    emote = {"[doge]": entry}
                    expected = _render_reply_content_py("[doge]", emote)
                    self.assertIn("/proxy/pic/i0.hdslb.com/doge.png", expected)

    def test_does_not_skip_truthy_non_string_url(self):
        emote = {"[doge]": {"webp_url": True, "url": "https://i0.hdslb.com/doge.png"}}
        self.assertEqual(_render_reply_content_py("[doge]", emote), "[doge]")

    def test_rejects_raw_whitespace_and_prohibited_characters(self):
        urls = [
            "https://i0.hdslb.com/bfs/emote/doge test.png",
            "https://i0.hdslb.com/bfs/emote/doge<script>.png",
            'https://i0.hdslb.com/bfs/emote/doge"quote.png',
            "https://i0.hdslb.com/bfs/emote/doge`tick.png",
            "https://i0.hdslb.com/bfs/emote/doge{brace}.png",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(_proxied_emote_src(url))


if __name__ == "__main__":
    unittest.main()
