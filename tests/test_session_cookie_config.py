# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miku.core.app import create_app
from miku.core.config import load_config


class TestSessionCookieConfig(unittest.TestCase):
    def test_cookie_security_matches_deployment_configuration(self):
        root = Path(__file__).resolve().parents[1]
        sample = (root / "config.toml.sample").read_text()
        cases = [
            ("default", None, "", False),
            ("sample", None, sample, False),
            ("http_env", "http://localhost:8888", "", False),
            ("https_env", "https://miku.example", "", True),
            ("https_env_with_sample", "https://miku.example", sample, True),
            ("https_config", None, '[site]\nsite_url = "https://miku.example"\n', True),
            ("explicit_true", "http://localhost:8888", "[quart]\nSESSION_COOKIE_SECURE = true\n", True),
            ("explicit_false", "https://miku.example", "[quart]\nSESSION_COOKIE_SECURE = false\n", False),
        ]
        base_env = {**os.environ, "USE_CRED": "false"}
        base_env.pop("SITE_URL", None)

        for name, site_url, config, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                cfg_path = Path(directory, "config.toml")
                cfg_path.write_text(config)
                env = base_env if site_url is None else {**base_env, "SITE_URL": site_url}
                with patch.dict(os.environ, env, clear=True):
                    appconf = load_config(cfg_path)
                    app = create_app(appconf)
                self.assertEqual(app.config["SESSION_COOKIE_SECURE"], expected)
                if site_url is not None:
                    self.assertEqual(appconf["site"]["site_url"], site_url)
