"""Client-side theme toggle regressions.

Run with PYTHONPATH=python .venv/bin/python -m unittest discover -s tests.

The theme toggle is fully client-side: localStorage is the single source of
truth, a synchronous <head> script applies `.dark` before first paint, and the
server no longer reads a `dark-theme` cookie nor exposes POST /toggle_theme.
"""

import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
BASE_HTML = ROOT / "templates" / "themes" / "modern" / "base.html"
THEMES_JS = ROOT / "static" / "themes" / "modern" / "js" / "themes.js"


class ThemeTemplateTests(unittest.TestCase):
    def test_no_server_theme_conditionals(self):
        src = BASE_HTML.read_text()
        # No Jinja-driven theming or server cookie references remain. (The
        # localStorage key "dark_mode" inside the JS pre-paint script is
        # client-side state and is covered by test_prepaint_script_present.)
        self.assertNotIn("dark_mode_pref", src)
        self.assertNotIn("dark-theme", src)
        self.assertNotIn("/toggle_theme", src)
        self.assertNotIn("if dark_mode", src)
        self.assertNotIn("dark_mode else", src)

    def test_html_tag_has_no_theme_class(self):
        src = BASE_HTML.read_text()
        self.assertRegex(src, r"<html[^>]*lang=\"\{\{ locale \}\}\"[^>]*>")

    def test_prepaint_script_present_and_nonces(self):
        src = BASE_HTML.read_text()
        m = re.search(r'<script nonce="\{\{ csp_nonce \}\}">(try\{var __m.*?)</script>', src)
        self.assertIsNotNone(m, "missing synchronous pre-paint theme script with CSP nonce")
        inline = m.group(1)
        # Reads the same helpers.storage encoding (encodeURIComponent(JSON.stringify)).
        self.assertIn('localStorage.getItem("dark_mode")', inline)
        self.assertIn("decodeURIComponent", inline)
        self.assertIn("prefers-color-scheme", inline)
        self.assertIn('classList.add("dark")', inline)
        # Must run before the stylesheet to avoid a flash of the wrong theme.
        self.assertLess(src.index(inline), src.index("main.css"))

    def test_prepaint_script_is_valid_js(self):
        import subprocess
        import tempfile

        src = BASE_HTML.read_text()
        inline = re.search(r'<script nonce="\{\{ csp_nonce \}\}">(try\{var __m.*?)</script>', src).group(1)
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=True) as f:
                f.write(inline)
                f.flush()
                proc = subprocess.run(
                    ["node", "--check", f.name],
                    capture_output=True,
                    timeout=30,
                )
        except FileNotFoundError:
            self.skipTest("node not available")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())


class ThemesJsTests(unittest.TestCase):
    def test_no_server_ping_or_pref_span(self):
        src = THEMES_JS.read_text()
        self.assertNotIn("/toggle_theme", src)
        self.assertNotIn("dark_mode_pref", src)
        head = src[src.index("function handleThemeToggle") : src.index("Mobile search toggle")]
        self.assertNotIn("csrf", head.lower())

    def test_toggle_is_localstorage_only(self):
        src = THEMES_JS.read_text()
        m = re.search(r"function handleThemeToggle\(e\) \{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("helpers.storage.get(STORAGE_KEY_THEME)", body)
        self.assertIn("helpers.storage.set(STORAGE_KEY_THEME, newTheme)", body)
        self.assertIn("setTheme(newTheme)", body)
        self.assertNotIn("xhr", body)
        self.assertNotIn("fetch", body)

    def test_init_prefers_localstorage(self):
        src = THEMES_JS.read_text()
        m = re.search(r"function initPreferences\(\) \{(.*?)\n  function initOpenccConvert", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("helpers.storage.get(STORAGE_KEY_THEME)", m.group(1))
        self.assertIn("setTheme(storedTheme)", m.group(1))


class ThemeServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_toggle_theme_route(self):
        import app as app_module  # noqa: F401  (registers routes)
        import shared

        rules = [str(r) for r in shared.app.url_map.iter_rules()]
        self.assertNotIn("/toggle_theme", rules)
        self.assertNotIn("toggle_theme_api", shared.app.view_functions)

    async def test_render_ignores_dark_theme_cookie(self):
        import shared

        contexts = []
        for cookie in (None, "dark-theme=1", "dark-theme=0"):
            headers = {"Cookie": cookie} if cookie else {}
            async with shared.app.test_request_context("/", headers=headers):
                with patch.object(shared, "render_template", AsyncMock(return_value="ok")) as render:
                    await shared.render_template_with_theme("home.html", videos=[])
                    contexts.append((render.await_args.args, dict(render.await_args.kwargs)))
        for args, kwargs in contexts:
            self.assertEqual(args[0], "themes/modern/home.html")
            self.assertNotIn("dark_mode", kwargs)
        # Cookie value must not change the rendered output at all (lambdas
        # are recreated per call, so compare only non-callable context).
        plain = [{k: v for k, v in kwargs.items() if not callable(v)} for _, kwargs in contexts]
        self.assertEqual(plain[0], plain[1])
        self.assertEqual(plain[1], plain[2])


if __name__ == "__main__":
    unittest.main()
