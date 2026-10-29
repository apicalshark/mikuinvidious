from pathlib import Path
import unittest

from extra import sanitize_html
import rtoml
from zhconv_rs import zhconv


class TestDependencies(unittest.TestCase):
    def test_nh3_sanitize_html_xss(self):
        # Disallow dangerous script tags
        dirty = '<div><script>alert("xss")</script><p>Safe content</p></div>'
        clean = sanitize_html(dirty)
        self.assertNotIn("<script>", clean)
        self.assertNotIn("alert", clean)
        self.assertIn("Safe content", clean)

    def test_nh3_sanitize_html_styles(self):
        # Allow safe styles, strip disallowed styles like position
        html_input = '<span style="color: red; position: fixed; font-weight: bold;">text</span>'
        clean = sanitize_html(html_input)
        self.assertIn("color:red", clean)
        self.assertIn("font-weight:bold", clean)
        self.assertNotIn("position", clean)

    def test_nh3_sanitize_html_allowed_tags_and_attrs(self):
        html_input = '<a href="https://example.com" title="Test" onclick="bad()">Link</a>'
        clean = sanitize_html(html_input)
        self.assertIn('href="https://example.com"', clean)
        self.assertIn('title="Test"', clean)
        self.assertNotIn("onclick", clean)

    def test_rtoml_load_config_sample(self):
        sample_path = Path("config.toml.sample")
        data = rtoml.load(sample_path)
        self.assertIsInstance(data, dict)
        self.assertIn("site", data)
        self.assertIn("server", data)
        self.assertIn("credential", data)

    def test_zhconv_rs_conversion(self):
        simplified = zhconv("進擊的巨人", "zh-hans")
        traditional = zhconv("进击的巨人", "zh-hant")
        self.assertEqual(simplified, "进击的巨人")
        self.assertEqual(traditional, "進擊的巨人")


if __name__ == "__main__":
    unittest.main()
