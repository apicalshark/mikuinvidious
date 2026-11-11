import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREF_HTML = ROOT / "templates" / "themes" / "modern" / "pref.html"

class TestM3Preferences(unittest.TestCase):
    def test_preferences_page_has_color_palette_picker(self):
        html = PREF_HTML.read_text(encoding="utf-8")
        self.assertIn('id="m3-seed-picker"', html)
        self.assertIn('data-seed="#a85383"', html)  # Sakura
        self.assertIn('data-seed="#fb7299"', html)  # Bilibili Pink
        self.assertIn('data-seed="#00aeec"', html)  # Bilibili Cyan
        self.assertIn('data-seed="#39c5bb"', html)  # Miku Cyan
        self.assertIn('data-seed="#ff7a00"', html)  # Orange

