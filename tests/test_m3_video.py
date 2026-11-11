import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
VIDEO_HTML = ROOT / "templates" / "themes" / "modern" / "video.html"

class TestM3Video(unittest.TestCase):
    def setUp(self):
        self.env = Environment(loader=FileSystemLoader("templates"))
        self.env.globals["_"] = lambda s: s
        self.env.filters["pic"] = lambda s: s
        self.env.filters["date"] = lambda s: s
        self.env.filters["intsep"] = lambda s: str(s)
        self.env.filters["format_desc"] = lambda s: s
        self.env.filters["reply_content"] = lambda s: s
        self.env.filters["secdur"] = lambda s: str(s)

    def test_video_template_syntax_and_structure(self):
        html = VIDEO_HTML.read_text(encoding="utf-8")
        # Invariant: YouTube 2-column layout preserved
        self.assertIn("lg:col-span-3", html)
        self.assertIn("lg:col-span-1", html)
        # Template compiles cleanly
        template = self.env.get_template("themes/modern/video.html")
        self.assertIsNotNone(template)

    def test_video_template_uses_m3_surface_container(self):
        html = VIDEO_HTML.read_text(encoding="utf-8")
        self.assertIn("bg-surface-container-low", html)

    def test_video_template_renders_with_mock_context(self):
        template = self.env.get_template("themes/modern/video.html")
        context = {
            "vinfo": {
                "bvid": "BV1test",
                "title": "Test Video Title",
                "pic": "http://example.com/pic.jpg",
                "desc": "Test Description",
                "pubdate": 1600000000,
                "tid": 1,
                "tname": "Animation",
                "stat": {
                    "view": 10000,
                    "like": 500,
                    "coin": 200,
                    "favorite": 100,
                    "share": 50,
                    "danmaku": 30,
                },
                "owner": {
                    "mid": 123456,
                    "name": "Test Creator",
                    "face": "http://example.com/face.jpg",
                },
            },
            "vset": [{"page": 1, "part": "P1", "cid": 123}],
            "idx": 0,
            "bvid": "BV1test",
            "is_live": False,
            "vtags": [{"tag_name": "Tag1"}],
            "related": [],
            "audio_only": False,
            "dash": None,
            "user": None,
            "asset_version": "1",
            "active_tab": "video",
            "i18n_catalog": {},
            "csp_nonce": "test-nonce",
            "locale": "en",
            "vcomments": {"page": {"count": 10}, "replies": []},
            "opencc_enabled": False,
            "vfavored": False,
        }
        rendered = template.render(**context)
        self.assertIn("Test Video Title", rendered)
        self.assertIn("Test Creator", rendered)
        self.assertIn("title=\"Likes\"", rendered)

