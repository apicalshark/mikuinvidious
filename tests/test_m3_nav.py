import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_HTML = ROOT / "templates" / "themes" / "modern" / "base.html"

class TestM3Nav(unittest.TestCase):
    def test_nav_contains_mobile_bottom_bar(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="m3-bottom-nav"', html)
        self.assertIn('href="/bangumi"', html)
        self.assertIn('href="/preferences"', html)

    def test_language_menu_uses_popover_api(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn('popover="auto"', html)
        self.assertIn('id="lang_menu"', html)
        self.assertIn('popovertarget="lang_menu"', html)
