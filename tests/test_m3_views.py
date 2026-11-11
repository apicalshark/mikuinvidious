import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent

class TestM3Views(unittest.TestCase):
    def setUp(self):
        self.env = Environment(loader=FileSystemLoader("templates"))
        self.env.globals["_"] = lambda s: s
        self.env.filters["pic"] = lambda s: s
        self.env.filters["date"] = lambda s: s
        self.env.filters["intsep"] = lambda s: str(s)
        self.env.filters["secdur"] = lambda s: str(s)
        self.env.filters["format_desc"] = lambda s: s
        self.env.filters["reply_content"] = lambda s: s

    def test_all_secondary_templates_compile(self):
        templates = [
            "themes/modern/space.html",
            "themes/modern/bangumi_index.html",
            "themes/modern/bangumi_view.html",
            "themes/modern/search.html",
            "themes/modern/search_article.html",
            "themes/modern/search_user.html",
            "themes/modern/convert.html",
        ]
        for t in templates:
            tmpl = self.env.get_template(t)
            self.assertIsNotNone(tmpl)

    def test_secondary_views_contain_m3_surface_container(self):
        for name in ["space.html", "bangumi_index.html", "search.html", "convert.html"]:
            path = ROOT / "templates" / "themes" / "modern" / name
            html = path.read_text(encoding="utf-8")
            self.assertIn("surface-container", html, f"{name} lacks M3 surface-container tokens")

    def test_search_category_and_sort_unified_across_types(self):
        for name in ["search.html", "search_article.html", "search_user.html"]:
            path = ROOT / "templates" / "themes" / "modern" / name
            html = path.read_text(encoding="utf-8")
            self.assertIn("&t=live", html, f"{name} is missing Live category button")
            self.assertIn("&t=video", html, f"{name} is missing Videos category button")
            self.assertIn("&t=article", html, f"{name} is missing Articles category button")
            self.assertIn("&t=user", html, f"{name} is missing Creators category button")
            self.assertIn("rounded-full!", html, f"{name} does not use unified rounded-full! button style")
