import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_HTML = ROOT / "templates" / "themes" / "modern" / "base.html"
M3_THEME_JS = ROOT / "static" / "themes" / "modern" / "js" / "m3_theme.js"


class TestM3Theme(unittest.IsolatedAsyncioTestCase):
    def test_base_template_has_m3_surface_container_tokens(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn("--md-sys-color-surface-container-low", html)
        self.assertIn("--md-sys-color-surface-container-high", html)
        self.assertIn("m3_seed", html)

    def test_base_template_preserves_original_accent_colors(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        # Original light mode Sakura primary
        self.assertIn("--theme-primary: #a85383;", html)
        # Original dark mode Sakura primary
        self.assertIn("--theme-primary: #ff97b5;", html)

    def test_popover_anchor_positioning_defined_in_base_css(self):
        css = (ROOT / "static" / "themes" / "modern" / "css" / "tailwind-input.css").read_text(encoding="utf-8")
        self.assertIn("anchor-name: --lang-btn", css)
        self.assertIn("position-anchor: --lang-btn", css)
        self.assertIn("right: anchor(right)", css)

    def test_m3_theme_js_preserves_original_accent_colors_for_default(self):
        js = M3_THEME_JS.read_text(encoding="utf-8")
        self.assertIn('"--theme-primary": "#a85383"', js)
        self.assertIn('"--theme-primary": "#ff97b5"', js)
        self.assertIn("isDefault", js)

    def test_m3_theme_js_retains_stored_seed_on_null_hex_theme_switch(self):
        js = M3_THEME_JS.read_text(encoding="utf-8")
        # Ensure passing null to applyTokens reads from getStoredSeed()
        self.assertIn('const seed = hex && typeof hex === "string" ? hex : getStoredSeed();', js)

    def test_dark_mode_surface_container_low_uses_dark_oklch_level(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn("--md-sys-color-surface-container-low: oklch(0.13 0.014 350);", html)
        js = M3_THEME_JS.read_text(encoding="utf-8")
        self.assertIn('"--md-sys-color-surface-container-low": "oklch(0.13 0.014 350)"', js)

    def test_light_mode_custom_seed_uses_balanced_primary(self):
        js = M3_THEME_JS.read_text(encoding="utf-8")
        self.assertIn('"--md-sys-color-primary": balancedPrimary', js)
        self.assertIn("const targetL = Math.min(L, 0.61);", js)

    def test_base_template_includes_default_accent_meta(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn('name="default-accent"', html)
        self.assertIn("default_accent", html)

    def test_default_accent_env_configuration_in_shared(self):
        import miku.state as shared

        self.assertIn("default_accent", shared.appconf["display"])
        self.assertTrue(shared.appconf["display"]["default_accent"].startswith("#"))

    def test_m3_theme_js_in_head_and_runs_immediately(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        head_section = html[: html.index("</head>")]
        self.assertIn("m3_theme.js", head_section)
        # Should not be deferred at bottom
        body_section = html[html.index("</head>") :]
        self.assertNotIn("m3_theme.js", body_section)

        js = M3_THEME_JS.read_text(encoding="utf-8")
        # Must execute init() directly without waiting for DOMContentLoaded
        self.assertNotIn('document.addEventListener("DOMContentLoaded", init)', js)

    def test_prepaint_script_sets_primary_css_variables(self):
        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn('setProperty("--theme-primary"', html)
        self.assertIn('setProperty("--md-sys-color-primary"', html)

    async def test_cookie_m3_seed_in_shared_render(self):
        from unittest.mock import AsyncMock, patch

        import miku.state as shared

        headers = {"Cookie": "m3_seed=%2300aeec"}
        async with shared.app.test_request_context("/search", headers=headers):
            with patch.object(shared, "render_template", AsyncMock(return_value="ok")) as render:
                await shared.render_template_with_theme("search.html")
                kwargs = dict(render.await_args.kwargs)
                self.assertEqual(kwargs.get("user_accent"), "#00aeec")
                self.assertEqual(kwargs.get("default_accent"), shared.appconf["display"]["default_accent"])

    async def test_crafted_m3_seed_cookies_rejected_and_tojson_escaped(self):
        from unittest.mock import AsyncMock, patch

        import miku.state as shared

        for bad_seed in ('#"a', '#";xss;//', "#12345z", "#12345"):
            headers = {"Cookie": f"m3_seed={bad_seed}"}
            async with shared.app.test_request_context("/search", headers=headers):
                with patch.object(shared, "render_template", AsyncMock(return_value="ok")) as render:
                    await shared.render_template_with_theme("search.html")
                    kwargs = dict(render.await_args.kwargs)
                    self.assertIsNone(kwargs.get("user_accent"), f"Accepted bad seed: {bad_seed}")

        # Uppercase hex is normalized to lowercase
        async with shared.app.test_request_context("/search", headers={"Cookie": "m3_seed=%23AABBCC"}):
            with patch.object(shared, "render_template", AsyncMock(return_value="ok")) as render:
                await shared.render_template_with_theme("search.html")
                kwargs = dict(render.await_args.kwargs)
                self.assertEqual(kwargs.get("user_accent"), "#aabbcc")

        # base.html serializes __ua and __da with | tojson (no outer double quotes)
        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn("var __ua={{ (user_accent or '') | tojson }};", html)
        self.assertIn("var __da={{ (default_accent or '#a85383') | tojson }};", html)
        self.assertNotIn('var __ua="{{', html)
        self.assertNotIn('var __da="{{', html)
        self.assertEqual(html.count('rel="jslicense"'), 1)

        # Rendering base.html with crafted accents safely JSON-escapes inside <script>
        from quart import render_template

        async with shared.app.test_request_context("/"):
            rendered = await render_template(
                "themes/modern/licenses.html",
                locale="en",
                asset_version="test",
                app_version="test",
                git_commit="",
                commit_url="",
                proxy_status={},
                _=lambda s: s,
                gettext=lambda s: s,
                ngettext=lambda s, p, n: s if n == 1 else p,
                i18n_catalog={},
                site_name="MikuInvidious",
                user_accent='";alert(1);//',
                default_accent="</script><script>alert(2)</script>",
            )
            self.assertNotIn('var __ua="";alert(1);//"', rendered)
            self.assertNotIn("</script><script>alert(2)</script>", rendered)
            self.assertIn("var __ua=", rendered)
            self.assertIn("var __da=", rendered)

    async def test_site_allow_download_string_normalized(self):
        from unittest.mock import AsyncMock, patch

        import miku.state as shared

        orig_site = dict(shared.appconf.get("site", {}))
        try:
            shared.appconf["site"]["site_allow_download"] = "false"
            async with shared.app.test_request_context("/"):
                with patch.object(shared, "render_template", AsyncMock(return_value="ok")) as render:
                    await shared.render_template_with_theme("home.html")
                    kwargs = dict(render.await_args.kwargs)
                    self.assertIs(kwargs.get("site_allow_download"), False)
        finally:
            shared.appconf["site"] = orig_site

    def test_bilibili_pink_distinguished_from_sakura(self):
        js = M3_THEME_JS.read_text(encoding="utf-8")
        self.assertIn("isSakura", js)
        self.assertIn("if (isSakura(hex))", js)
        # Bilibili pink hex code
        self.assertIn('bili_pink: "#fb7299"', js)

    def test_light_mode_card_container_has_subtly_white_tint(self):
        js = M3_THEME_JS.read_text(encoding="utf-8")
        self.assertIn('"oklch(0.992 0.004 350)"', js)
        self.assertIn("cLow = Math.max(0.003", js)

    def test_video_player_follows_m3_accent_tokens(self):
        js = M3_THEME_JS.read_text(encoding="utf-8")
        self.assertIn('"--player-primary"', js)
        self.assertIn('"--player-on-primary"', js)
        self.assertIn('"--media-primary-color"', js)

        player_css = (ROOT / "static" / "themes" / "modern" / "css" / "player.css").read_text(encoding="utf-8")
        self.assertIn("var(--player-primary", player_css)
        self.assertIn("--media-primary-color: var(--player-primary", player_css)
        self.assertIn("--media-range-bar-color: var(--media-primary-color);", player_css)
        self.assertIn("#quality-list button.active", player_css)

        html = BASE_HTML.read_text(encoding="utf-8")
        self.assertIn('setProperty("--player-primary"', html)
        self.assertIn('setProperty("--media-primary-color"', html)
