import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestSessionCookieConfig(unittest.TestCase):
    def test_cookie_security_matches_deployment_configuration(self):
        root = Path(__file__).resolve().parents[1]
        sample = (root / "config.toml.sample").read_text()
        cases = [
            ("default", None, "", False),
            ("sample", None, sample, False),
            ("http_env", "http://localhost:8888", "", False),
            ("https_env", "https://miku.example", "", True),
            ("https_config", None, '[site]\nsite_url = "https://miku.example"\n', True),
            ("explicit_true", "http://localhost:8888", "[quart]\nSESSION_COOKIE_SECURE = true\n", True),
            ("explicit_false", "https://miku.example", "[quart]\nSESSION_COOKIE_SECURE = false\n", False),
        ]
        env = {**os.environ, "PYTHONPATH": str(root / "python"), "USE_CRED": "false"}
        env.pop("SITE_URL", None)
        for name, site_url, config, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                Path(directory, "config.toml").write_text(config)
                result = subprocess.run(
                    [sys.executable, "-c", "from shared import app; print(app.config['SESSION_COOKIE_SECURE'])"],
                    cwd=directory,
                    env=env if site_url is None else {**env, "SITE_URL": site_url},
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                self.assertEqual(result.stdout.strip().splitlines()[-1], str(expected))
