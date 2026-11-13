# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.
import unittest

from miku.ui.sanitizer import sanitize_html


class TestSanitizer(unittest.TestCase):
    def test_strip_script_tag(self):
        malicious = '<p>Hello</p><script>alert("xss")</script>'
        cleaned = sanitize_html(malicious)
        self.assertNotIn("<script>", cleaned)
        self.assertNotIn("alert", cleaned)
        self.assertIn("<p>Hello</p>", cleaned)

    def test_preserve_safe_tags(self):
        safe_input = '<p><strong>Bold</strong> and <em>italic</em> with <a href="https://example.com">link</a></p>'
        cleaned = sanitize_html(safe_input)
        self.assertIn("<strong>Bold</strong>", cleaned)
        self.assertIn("<em>italic</em>", cleaned)
        self.assertIn('href="https://example.com"', cleaned)

    def test_style_filtering(self):
        styled = '<span style="color: red; position: fixed; top: 0;">Text</span>'
        cleaned = sanitize_html(styled)
        self.assertIn("color", cleaned)
        self.assertNotIn("position", cleaned)

    def test_link_rel_noopener_noreferrer(self):
        cleaned = sanitize_html('<a href="https://example.com" rel="opener">Link</a>')
        self.assertIn('rel="noopener noreferrer"', cleaned)

    def test_class_restricted_to_img_code_pre(self):
        html = (
            '<p class="evil-cls" style="text-align: center;">Para</p>'
            '<div class="evil-cls" style="color: blue;">Div</div>'
            '<span class="evil-cls" style="color: red;">Span</span>'
            '<h1 class="evil-cls" style="color: green;">H1</h1>'
            '<img src="/proxy/pic/a.png" class="mx-auto" style="width: 1.2em; display: inline-block;">'
            '<pre class="code-wrap"><code class="language-python">x = 1</code></pre>'
        )
        cleaned = sanitize_html(html)
        self.assertNotIn("evil-cls", cleaned)
        self.assertIn('class="mx-auto"', cleaned)
        self.assertIn('class="language-python"', cleaned)
        self.assertIn('class="code-wrap"', cleaned)
        self.assertIn("text-align:center", cleaned.replace(": ", ":"))
        self.assertIn("width:1.2em", cleaned.replace(": ", ":"))
        self.assertIn("color:green", cleaned.replace(": ", ":"))

    def test_disallowed_attributes_on_wildcard_tags_stripped(self):
        cleaned = sanitize_html('<strong class="bad" style="color: red;" onclick="alert(1)">Bold</strong>')
        self.assertNotIn("bad", cleaned)
        self.assertNotIn("color", cleaned)
        self.assertNotIn("onclick", cleaned)
        self.assertEqual(cleaned, "<strong>Bold</strong>")


if __name__ == "__main__":
    unittest.main()
