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


if __name__ == "__main__":
    unittest.main()
