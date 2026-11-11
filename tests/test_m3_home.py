import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME_HTML = ROOT / "templates" / "themes" / "modern" / "home.html"

class TestM3Home(unittest.TestCase):
    def test_home_page_renders_m3_cards(self):
        html = HOME_HTML.read_text(encoding="utf-8")
        self.assertIn("bg-surface-container-low", html)
        self.assertIn("m3_card", html)
