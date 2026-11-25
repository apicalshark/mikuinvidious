# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import unittest
from unittest.mock import patch

import miku.core.rate_limit as rate_limit
from miku.core.config import get_default_config
from quart import Quart


class TestRateLimitConfig(unittest.IsolatedAsyncioTestCase):
    def test_env_overrides_default_rate_limit_profiles(self) -> None:
        with patch.dict(
            os.environ,
            {
                "RATE_LIMIT_NORMAL_LIMIT": "120",
                "RATE_LIMIT_NORMAL_WINDOW": "30",
                "RATE_LIMIT_SEARCH_LIMIT": "invalid",
                "RATE_LIMIT_SEARCH_WINDOW": "60",
            },
        ):
            conf = get_default_config()
            self.assertEqual(conf["rate_limit"]["limits"]["normal"], {"limit": 120, "window": 30})
            self.assertEqual(conf["rate_limit"]["limits"]["search"], {"limit": 20, "window": 60})

    def test_resolve_rate_limit_falls_back_on_invalid_values(self) -> None:
        with patch.dict(
            rate_limit.appconf["rate_limit"],
            {"limits": {"normal": {"limit": float("inf"), "window": -5}}},
        ):
            self.assertEqual(rate_limit.resolve_rate_limit("normal", 60, 60), (60, 60))

        with patch.dict(
            rate_limit.appconf["rate_limit"],
            {"limits": {"normal": {"limit": 150, "window": 90}}},
        ):
            self.assertEqual(rate_limit.resolve_rate_limit("normal", 60, 60), (150, 90))

    async def test_decorator_uses_configured_profile_limits_dynamically(self) -> None:
        app = Quart("test_rl_config")

        @app.route("/ping")
        @rate_limit.rate_limit(**rate_limit.RATE_LIMITS["strict"])
        async def ping():
            return "ok"

        limiter = rate_limit.RateLimiter(redis_client=None)
        with (
            patch.dict(
                rate_limit.appconf["rate_limit"],
                {"enabled": True, "limits": {"strict": {"limit": 2, "window": 60}}},
            ),
            patch.object(rate_limit, "get_rate_limiter", return_value=limiter),
        ):
            client = app.test_client()
            self.assertEqual((await client.get("/ping")).status_code, 200)
            self.assertEqual((await client.get("/ping")).status_code, 200)
            self.assertEqual((await client.get("/ping")).status_code, 429)
