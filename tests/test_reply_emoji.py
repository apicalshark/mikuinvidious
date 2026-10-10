"""Run with PYTHONPATH=python .venv/bin/python -m unittest discover -s tests.

Covers the safe reply-emoji renderer (transformers.render_reply_content):
Bilibili [...] placeholders become same-origin proxied <img> tags, and no
upstream HTML, JS, URL, or tracking param may reach the client.
"""

import unittest

from transformers import _proxied_emote_src, render_reply_content

DOGE = "https://i0.hdslb.com/bfs/emote/3087d273a78ccaff4bb1e9972e2ba2a7583c9f11.png"


def content(message, emote=None, **kw):
    c = {"message": message}
    if emote is not None:
        c["emote"] = emote
    c.update(kw)
    return c


def doge_entry(**over):
    e = {"url": DOGE, "meta": {"size": 1}}
    e.update(over)
    return e


class ProxiedEmoteSrcTests(unittest.TestCase):
    def test_allows_hdslb_host(self):
        self.assertEqual(
            _proxied_emote_src(DOGE),
            "/proxy/pic/i0.hdslb.com/bfs/emote/3087d273a78ccaff4bb1e9972e2ba2a7583c9f11.png",
        )

    def test_allows_protocol_relative(self):
        self.assertEqual(
            _proxied_emote_src("//i0.hdslb.com/bfs/emote/a.png"),
            "/proxy/pic/i0.hdslb.com/bfs/emote/a.png",
        )

    def test_strips_tracking_query_and_fragment(self):
        src = _proxied_emote_src(DOGE + "?token=secret&uid=1#frag")
        self.assertEqual(src, "/proxy/pic/i0.hdslb.com/bfs/emote/3087d273a78ccaff4bb1e9972e2ba2a7583c9f11.png")
        self.assertNotIn("?", src or "")
        self.assertNotIn("secret", src or "")

    def test_rejects_foreign_host(self):
        self.assertIsNone(_proxied_emote_src("https://evil.example/x.png"))
        self.assertIsNone(_proxied_emote_src("https://hdslb.com.evil.example/x.png"))

    def test_rejects_non_http_scheme(self):
        self.assertIsNone(_proxied_emote_src("javascript:alert(1)"))
        self.assertIsNone(_proxied_emote_src("data:image/png;base64,AAAA"))
        self.assertIsNone(_proxied_emote_src("ftp://i0.hdslb.com/x.png"))

    def test_rejects_attribute_breakout_and_traversal(self):
        self.assertIsNone(_proxied_emote_src('https://i0.hdslb.com/x".png'))
        self.assertIsNone(_proxied_emote_src("https://i0.hdslb.com/x y.png"))
        self.assertIsNone(_proxied_emote_src("https://i0.hdslb.com/../x.png"))

    def test_rejects_garbage(self):
        for bad in (None, "", 42, {}, "not a url", "https://"):
            self.assertIsNone(_proxied_emote_src(bad))


class RenderReplyContentTests(unittest.TestCase):
    def test_plain_message_is_escaped(self):
        out = render_reply_content(content('<script>alert("x")</script>'))
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_size1_emote_proxied_inline(self):
        out = render_reply_content(content("hi [doge]", {"[doge]": doge_entry()}))
        self.assertIn('src="/proxy/pic/i0.hdslb.com/bfs/emote/', out)
        self.assertIn('alt="[doge]"', out)
        self.assertIn("1.4em", out)
        self.assertIn('loading="lazy"', out)
        self.assertNotIn("https://i0.hdslb.com", out)  # no direct upstream URL leaks

    def test_size2_emote_block_style_without_data_attrs(self):
        out = render_reply_content(content(
            "[sticker]",
            {"[sticker]": doge_entry(meta={"size": 2}, jump_url="https://mall.bilibili.com/x")},
        ))
        self.assertIn("50px", out)
        self.assertNotIn("1.4em", out)
        self.assertNotIn("emoji-jump-url", out)
        self.assertNotIn("mall.bilibili.com", out)

    def test_prefers_webp_then_gif_then_url(self):
        entry = doge_entry(
            url="https://i0.hdslb.com/bfs/emote/u.png",
            gif_url="https://i0.hdslb.com/bfs/emote/g.gif",
            webp_url="https://i0.hdslb.com/bfs/emote/w.webp",
        )
        out = render_reply_content(content("[e]", {"[e]": entry}))
        self.assertIn("/proxy/pic/i0.hdslb.com/bfs/emote/w.webp", out)
        entry.pop("webp_url")
        out = render_reply_content(content("[e]", {"[e]": entry}))
        self.assertIn("/proxy/pic/i0.hdslb.com/bfs/emote/g.gif", out)

    def test_unknown_token_stays_text(self):
        out = render_reply_content(content("a [nope] b", {}))
        self.assertEqual(out, "a [nope] b")
        out = render_reply_content(content("a [nope] b", {"[other]": doge_entry()}))
        self.assertEqual(out, "a [nope] b")

    def test_blocked_url_stays_text(self):
        out = render_reply_content(content(
            "x [evil]", {"[evil]": doge_entry(url="https://evil.example/x.png")}
        ))
        self.assertNotIn("<img", out)
        self.assertIn("[evil]", out)

    def test_xss_in_message_with_emote(self):
        out = render_reply_content(content(
            '"><img src=x onerror=alert(1)> [doge]', {"[doge]": doge_entry()}
        ))
        self.assertIn("&quot;&gt;", out)
        self.assertIn("&lt;img src=x", out)  # attacker tag stays inert text
        self.assertEqual(out.count("<img"), 1)  # only our own tag

    def test_alt_is_escaped(self):
        out = render_reply_content(content('["q] hmm', {}))
        self.assertIn('[&quot;q] hmm', out)

    def test_no_double_escape_when_emote_present(self):
        # Regression: substitution must run on the raw message so fallback
        # tokens are escaped exactly once (CodeRabbit PR #63 review).
        out = render_reply_content(content('["q] [doge]', {"[doge]": doge_entry()}))
        self.assertIn('[&quot;q]', out)
        self.assertNotIn("&amp;quot;", out)
        self.assertIn("<img", out)

    def test_emote_name_with_special_char_matches(self):
        key = "[a&b]"
        entry = doge_entry()
        out = render_reply_content(content(f"x {key} y", {key: entry}))
        self.assertIn("<img", out)
        self.assertIn('alt="[a&amp;b]"', out)  # escaped once, inside the attribute
        self.assertNotIn(">[a", out)  # no leftover visible token text

    def test_degenerate_inputs(self):
        self.assertEqual(render_reply_content(None), "")
        self.assertEqual(render_reply_content(42), "")
        self.assertEqual(render_reply_content("plain <b>"), "plain &lt;b&gt;")
        self.assertEqual(render_reply_content({"message": None}, ), "")
        self.assertEqual(render_reply_content({"message": "[doge]"}), "[doge]")


if __name__ == "__main__":
    unittest.main()
